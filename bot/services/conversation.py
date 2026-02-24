"""会話ログ管理モジュール。
会話履歴のメモリ保持・永続化・検索を行う。

- 直近の会話（最大20ターン）をメモリに保持
- 全会話をconversation_log.jsonlに永続化
- Claude Code呼び出し時にコンテキストとして渡す
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from collections import deque
from bot.utils.paths import CONVERSATION_LOG_FILE

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# メモリに保持する最大ターン数
MAX_MEMORY_TURNS = 20


class ConversationManager:
    """会話ログの管理クラス。"""

    def __init__(self):
        self._history = deque(maxlen=MAX_MEMORY_TURNS)
        self._ensure_log_file()

    def _ensure_log_file(self):
        """ログファイルのディレクトリを作成。"""
        CONVERSATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not CONVERSATION_LOG_FILE.exists():
            CONVERSATION_LOG_FILE.touch()
            logger.info(f"会話ログファイル作成: {CONVERSATION_LOG_FILE}")

    def add_user_message(self, content: str, channel: str = ""):
        """ユーザーメッセージを記録。

        Args:
            content: メッセージ内容
            channel: チャンネル名
        """
        entry = {
            "role": "user",
            "content": content,
            "channel": channel,
            "timestamp": datetime.now(JST).isoformat(),
        }
        self._history.append(entry)
        self._append_to_log(entry)

    def add_bot_response(self, content: str, risk_level: str = "",
                         duration_ms: int = 0, cost_usd: float = 0):
        """Bot応答を記録。

        Args:
            content: 応答内容
            risk_level: リスクレベル
            duration_ms: 応答時間（ミリ秒）
            cost_usd: APIコスト（USD）
        """
        entry = {
            "role": "assistant",
            "content": content[:500],  # ログには先頭500文字まで
            "risk_level": risk_level,
            "duration_ms": duration_ms,
            "cost_usd": cost_usd,
            "timestamp": datetime.now(JST).isoformat(),
        }
        self._history.append(entry)
        self._append_to_log(entry)

    def add_script_execution(self, script_id: str, success: bool,
                             duration_sec: float = 0, output: str = ""):
        """スクリプト実行を記録。"""
        entry = {
            "role": "system",
            "type": "script_execution",
            "script_id": script_id,
            "success": success,
            "duration_sec": duration_sec,
            "output": output[:200],
            "timestamp": datetime.now(JST).isoformat(),
        }
        self._history.append(entry)
        self._append_to_log(entry)

    def get_context(self, max_turns: int = 10) -> str:
        """Claude Codeに渡す会話コンテキストを生成。

        Args:
            max_turns: 含める最大ターン数

        Returns:
            str: コンテキスト文字列
        """
        recent = list(self._history)[-max_turns:]

        if not recent:
            return ""

        lines = ["--- 直近の会話履歴 ---"]
        for entry in recent:
            role = entry.get("role", "?")
            content = entry.get("content", "")
            timestamp = entry.get("timestamp", "")[:16]

            if role == "user":
                lines.append(f"[{timestamp}] しゅうた: {content}")
            elif role == "assistant":
                lines.append(f"[{timestamp}] Lex: {content}")
            elif role == "system":
                script_id = entry.get("script_id", "?")
                success = "成功" if entry.get("success") else "失敗"
                lines.append(
                    f"[{timestamp}] [スクリプト実行] {script_id}: {success}"
                )

        return "\n".join(lines)

    def get_stats(self) -> dict:
        """会話統計を取得。"""
        total_messages = 0
        total_cost = 0.0

        try:
            with open(CONVERSATION_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        total_messages += 1
                        total_cost += entry.get("cost_usd", 0)
                    except json.JSONDecodeError:
                        continue
        except IOError:
            pass

        return {
            "total_messages": total_messages,
            "memory_turns": len(self._history),
            "total_cost_usd": round(total_cost, 4),
        }

    def _append_to_log(self, entry: dict):
        """ログファイルにエントリを追記（JSONL形式）。"""
        try:
            with open(CONVERSATION_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except IOError as e:
            logger.error(f"会話ログ書き込みエラー: {e}")
