"""ヘルスモニタリングサービス。
Lex の健康状態を追跡し、異常を検知する。

Phase 2:
- CLI呼び出しの成功/失敗を記録
- 連続失敗、タイムアウト頻度の追跡
- 構造化エラーログ (error_log.jsonl)
- 自己修復トリガー判定
"""
import json
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot.utils.paths import DATA_DIR, ERROR_LOG_FILE

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# --- 自己修復トリガー閾値 ---
CONSECUTIVE_FAILURES_THRESHOLD = 3      # 連続失敗でアラート
TIMEOUTS_PER_HOUR_THRESHOLD = 5         # 1時間あたりのタイムアウト数でアラート
MAX_ERROR_HISTORY = 50                  # 保持するエラー履歴数

# ヘルス状態永続化ファイル
HEALTH_STATE_FILE = DATA_DIR / "health_state.json"


class HealthMonitor:
    """Bot の健康状態を追跡するモニター。"""

    def __init__(self):
        self._boot_time = datetime.now(JST)
        self._last_successful_cli_call = None
        self._consecutive_cli_failures = 0
        self._total_cli_calls = 0
        self._total_cli_errors = 0
        self._total_cli_timeouts = 0
        self._total_max_turns_hit = 0
        self._total_cost_session_usd = 0.0
        self._last_error = None
        self._last_error_time = None
        self._error_history = deque(maxlen=MAX_ERROR_HISTORY)
        self._gateway_disconnects = 0
        self._repair_state = None  # Phase 4: 修復状態

        # 前回のヘルス状態を読み込み（再起動後の継続性）
        self._load_state()

    def record_cli_success(self, cost_usd: float = 0, duration_sec: float = 0):
        """CLI呼び出し成功を記録。"""
        self._total_cli_calls += 1
        self._consecutive_cli_failures = 0
        self._last_successful_cli_call = datetime.now(JST)
        self._total_cost_session_usd += cost_usd

    def record_cli_failure(self, error_type: str, error_msg: str,
                           cost_usd: float = 0):
        """CLI呼び出し失敗を記録。

        Args:
            error_type: "timeout" / "max_turns" / "cli_error" / "auth_error" / "unexpected"
            error_msg: エラーメッセージ
            cost_usd: 発生したコスト

        Returns:
            bool: 自己修復をトリガーすべきかどうか
        """
        now = datetime.now(JST)
        self._total_cli_calls += 1
        self._total_cli_errors += 1
        self._consecutive_cli_failures += 1
        self._last_error = error_msg
        self._last_error_time = now
        self._total_cost_session_usd += cost_usd

        if error_type == "timeout":
            self._total_cli_timeouts += 1
        elif error_type == "max_turns":
            self._total_max_turns_hit += 1

        # エラー履歴に追加
        error_entry = {
            "timestamp": now.isoformat(),
            "error_type": error_type,
            "error_message": error_msg[:200],
            "cost_usd": cost_usd,
            "consecutive_failures": self._consecutive_cli_failures,
        }
        self._error_history.append(error_entry)

        # 構造化エラーログに書き込み
        self._write_error_log(error_entry)

        logger.warning(
            f"ヘルス: CLI失敗記録 type={error_type}, "
            f"連続失敗={self._consecutive_cli_failures}"
        )

        return self._consecutive_cli_failures >= CONSECUTIVE_FAILURES_THRESHOLD

    def record_gateway_disconnect(self):
        """Gateway切断を記録。"""
        self._gateway_disconnects += 1

    def needs_attention(self) -> tuple:
        """Botが注意を必要としているか判定。

        Returns:
            (needs_attention: bool, reason: str)
        """
        # 連続失敗チェック
        if self._consecutive_cli_failures >= CONSECUTIVE_FAILURES_THRESHOLD:
            return (True,
                    f"CLI連続失敗: {self._consecutive_cli_failures}回")

        # 1時間あたりのタイムアウト数チェック
        one_hour_ago = datetime.now(JST) - timedelta(hours=1)
        recent_timeouts = sum(
            1 for e in self._error_history
            if (e["error_type"] == "timeout"
                and datetime.fromisoformat(e["timestamp"]) > one_hour_ago)
        )
        if recent_timeouts >= TIMEOUTS_PER_HOUR_THRESHOLD:
            return (True,
                    f"1時間以内にタイムアウト{recent_timeouts}回")

        return (False, "")

    def get_health_report(self) -> dict:
        """ヘルスレポートを生成。"""
        now = datetime.now(JST)
        uptime = now - self._boot_time
        uptime_str = str(uptime).split('.')[0]  # マイクロ秒除去

        needs, reason = self.needs_attention()

        # 成功率計算
        if self._total_cli_calls > 0:
            success_rate = (
                (self._total_cli_calls - self._total_cli_errors)
                / self._total_cli_calls * 100
            )
        else:
            success_rate = 100.0

        status = "🔴 要注意" if needs else "🟢 正常"

        return {
            "status": status,
            "status_healthy": not needs,
            "attention_reason": reason,
            "uptime": uptime_str,
            "boot_time": self._boot_time.strftime("%Y-%m-%d %H:%M"),
            "total_cli_calls": self._total_cli_calls,
            "total_cli_errors": self._total_cli_errors,
            "total_timeouts": self._total_cli_timeouts,
            "total_max_turns": self._total_max_turns_hit,
            "consecutive_failures": self._consecutive_cli_failures,
            "success_rate": f"{success_rate:.1f}%",
            "total_cost_usd": self._total_cost_session_usd,
            "last_successful_call": (
                self._last_successful_cli_call.strftime("%H:%M:%S")
                if self._last_successful_cli_call else "なし"
            ),
            "last_error": self._last_error,
            "last_error_time": (
                self._last_error_time.strftime("%H:%M:%S")
                if self._last_error_time else None
            ),
            "gateway_disconnects": self._gateway_disconnects,
        }

    def get_error_context_for_repair(self) -> str:
        """自己修復用のエラーコンテキストを構築。"""
        lines = ["=== SELF-REPAIR CONTEXT ===\n"]

        # 直近エラー
        lines.append("[ERRORS - Recent]")
        for entry in list(self._error_history)[-10:]:
            lines.append(
                f"  {entry['timestamp']} | {entry['error_type']} | "
                f"{entry['error_message'][:100]}"
            )

        # ヘルスメトリクス
        report = self.get_health_report()
        lines.append(f"\n[HEALTH METRICS]")
        lines.append(f"  Status: {report['status']}")
        lines.append(f"  Uptime: {report['uptime']}")
        lines.append(f"  Consecutive failures: {report['consecutive_failures']}")
        lines.append(f"  Total errors: {report['total_cli_errors']}")
        lines.append(f"  Total cost: ${report['total_cost_usd']:.4f}")
        lines.append(f"  Success rate: {report['success_rate']}")

        return "\n".join(lines)

    def save_state(self):
        """ヘルス状態をディスクに永続化（再起動前に呼ぶ）。"""
        state = {
            "saved_at": datetime.now(JST).isoformat(),
            "total_cli_calls": self._total_cli_calls,
            "total_cli_errors": self._total_cli_errors,
            "total_cli_timeouts": self._total_cli_timeouts,
            "total_max_turns_hit": self._total_max_turns_hit,
            "total_cost_session_usd": self._total_cost_session_usd,
            "repair_state": self._repair_state,
        }
        try:
            HEALTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HEALTH_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info("ヘルス状態を保存しました")
        except Exception as e:
            logger.error(f"ヘルス状態保存エラー: {e}")

    def _load_state(self):
        """前回のヘルス状態を読み込み。"""
        if HEALTH_STATE_FILE.exists():
            try:
                with open(HEALTH_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._repair_state = state.get("repair_state")
                if self._repair_state:
                    logger.info(
                        f"修復状態を検出: {self._repair_state.get('description', '?')}"
                    )
            except Exception as e:
                logger.warning(f"ヘルス状態読み込みエラー（無視）: {e}")

    def set_repair_state(self, state: dict):
        """修復状態を設定（Phase 4: 再起動後の検証用）。"""
        self._repair_state = state
        self.save_state()

    def get_repair_state(self) -> dict:
        """修復状態を取得。"""
        return self._repair_state

    def clear_repair_state(self):
        """修復状態をクリア。"""
        self._repair_state = None
        self.save_state()

    def _write_error_log(self, entry: dict):
        """構造化エラーログに書き込み。"""
        try:
            ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"エラーログ書き込み失敗（無視）: {e}")
