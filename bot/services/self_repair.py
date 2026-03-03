"""自己修復サービス。
Lex が自身のエラーを診断し、修復する能力を提供する。

Phase 3: 診断 + 承認付き修復
Phase 4: Git セーフティネット + 自律修復

2段階方式:
  - 診断フェーズ: 読み取り専用でエラー原因を特定
  - 修復フェーズ: 承認後にコード変更を実行
"""
import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from bot.services.claude_cli import ClaudeCLIBridge
from bot.utils.paths import PROJECT_ROOT, LOGS_DIR

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# --- コスト制御 ---
MAX_REPAIR_COST_PER_DAY = 1.00       # 1日の自己修復予算上限
MAX_REPAIR_COST_PER_ATTEMPT = 0.50   # 1回の修復上限
MAX_REPAIR_ATTEMPTS_PER_HOUR = 3     # 1時間に最大3回
REPAIR_COOLDOWN_SECONDS = 300        # 修復試行後5分クールダウン

# --- 修復用システムプロンプト ---
REPAIR_SYSTEM_PROMPT = """あなたは Lex です。Discord Bot として動作しており、自分自身のエラーを診断・修復しています。
ソースコードはプロジェクトルート（PROJECT_ROOT）にあります。

## 重要ルール
1. .env やクレデンシャルファイルを絶対に変更しない
2. data/ ディレクトリのファイルを削除しない
3. 変更は最小限かつ的を射たものにする
4. 確信がない場合は診断結果の報告のみ行い、修復は試みない
5. 応答は日本語で行う

## アーキテクチャ
- bot/main.py: エントリーポイント（再起動ロジック）
- bot/lex_bot.py: Botクラス、Cog読み込み
- bot/services/claude_cli.py: Claude CLI subprocess ブリッジ
- bot/cogs/claude_bridge.py: メッセージルーティング
- bot/services/approval.py: リスク分類
- bot/services/conversation.py: 会話メモリ
- bot/services/health_monitor.py: ヘルスモニタリング
- bot/cogs/general.py: /restart, /ping, /status
- bot/cogs/daily_report.py: 定期報告
- bot/config.py: 環境変数設定
- bot/utils/paths.py: パス定義
"""

DIAGNOSIS_PROMPT_TEMPLATE = """以下のエラー情報を分析し、根本原因と修復方法を提案してください。

{context}

{log_tail}

## 出力形式（JSON）
```json
{{
    "diagnosis": "根本原因の簡潔な説明",
    "severity": "low / medium / high",
    "proposed_fixes": [
        {{
            "file": "対象ファイルのパス",
            "description": "具体的な修正内容",
            "risk": "low / medium / high"
        }}
    ],
    "can_auto_fix": true / false,
    "needs_restart": true / false,
    "summary": "オーナー向けの日本語要約（1-2文）"
}}
```
"""


class SelfRepairService:
    """Lex の自己修復を管理するサービス。"""

    def __init__(self, bot):
        self.bot = bot
        self.claude = ClaudeCLIBridge()
        self._repair_lock = asyncio.Lock()
        self._last_repair_attempt = None
        self._repair_attempts_today = 0
        self._repair_cost_today = 0.0
        self._last_reset_date = datetime.now(JST).date()

    async def diagnose(self, trigger: str = "manual") -> dict:
        """診断のみ実行（読み取り専用、安全）。

        Args:
            trigger: トリガー種別 ("manual", "auto_health", "user_request")

        Returns:
            dict: {"attempted": bool, "success": bool,
                   "diagnosis": dict or None, "message": str}
        """
        # クールダウンチェック
        if not self._check_cooldown():
            return {
                "attempted": False,
                "success": False,
                "diagnosis": None,
                "message": "⏳ クールダウン中です（前回の修復から5分以内）。",
            }

        # 予算チェック
        if not self._check_budget():
            return {
                "attempted": False,
                "success": False,
                "diagnosis": None,
                "message": "💰 本日の自己修復予算に達しました。",
            }

        async with self._repair_lock:
            return await self._run_diagnosis(trigger)

    async def attempt_repair(self, trigger: str = "user_request") -> dict:
        """診断 + 修復を実行。

        Args:
            trigger: トリガー種別

        Returns:
            dict: {"attempted": bool, "success": bool,
                   "actions_taken": list, "message": str}
        """
        # まず診断
        diag_result = await self.diagnose(trigger)
        if not diag_result["success"]:
            return {
                "attempted": diag_result["attempted"],
                "success": False,
                "actions_taken": [],
                "message": diag_result["message"],
            }

        diagnosis = diag_result["diagnosis"]

        # 自動修復不可の場合
        if not diagnosis.get("can_auto_fix", False):
            return {
                "attempted": True,
                "success": False,
                "actions_taken": [],
                "message": (
                    f"🔍 **診断結果**\n\n"
                    f"{diagnosis.get('summary', diagnosis.get('diagnosis', '不明'))}\n\n"
                    f"⚠️ この問題は自動修復できません。手動対応が必要です。"
                ),
            }

        # 修復実行（Phase 4: Git safety 付き）
        repair_result = await self._execute_repair(diagnosis)
        return repair_result

    async def _run_diagnosis(self, trigger: str) -> dict:
        """診断フェーズの実行。"""
        self._last_repair_attempt = datetime.now(JST)

        # コンテキスト構築
        health = getattr(self.bot, 'health_monitor', None)
        context = ""
        if health:
            context = health.get_error_context_for_repair()

        # ログ末尾取得
        log_tail = self._get_log_tail(50)

        # 診断プロンプト
        prompt = DIAGNOSIS_PROMPT_TEMPLATE.format(
            context=context if context else "[エラーコンテキストなし]",
            log_tail=f"[RECENT LOG]\n{log_tail}" if log_tail else "",
        )

        logger.info(f"自己診断開始 (trigger={trigger})")

        result = await self.claude.ask(
            prompt,
            system_prompt=REPAIR_SYSTEM_PROMPT,
            profile="complex",
            max_turns=5,
        )

        if not result["success"]:
            return {
                "attempted": True,
                "success": False,
                "diagnosis": None,
                "message": f"⚠️ 診断中にエラー: {result['error']}",
            }

        # 応答からJSONを抽出
        diagnosis = self._parse_diagnosis(result["text"])
        cost = result.get("cost_usd", 0)
        self._repair_cost_today += cost
        self._repair_attempts_today += 1

        if diagnosis:
            summary = diagnosis.get("summary", diagnosis.get("diagnosis", ""))
            fixes = diagnosis.get("proposed_fixes", [])
            fix_text = "\n".join(
                f"  • `{f['file']}`: {f['description']}"
                for f in fixes
            ) if fixes else "  （なし）"

            message = (
                f"🔍 **自己診断結果**\n\n"
                f"**原因:** {summary}\n"
                f"**深刻度:** {diagnosis.get('severity', '?')}\n"
                f"**自動修復:** {'可能' if diagnosis.get('can_auto_fix') else '不可'}\n"
                f"**再起動必要:** {'はい' if diagnosis.get('needs_restart') else 'いいえ'}\n\n"
                f"**提案修正:**\n{fix_text}\n\n"
                f"💰 診断コスト: ${cost:.4f}"
            )
            return {
                "attempted": True,
                "success": True,
                "diagnosis": diagnosis,
                "message": message,
            }
        else:
            return {
                "attempted": True,
                "success": False,
                "diagnosis": None,
                "message": (
                    f"🔍 診断を実行しましたが、構造化結果を取得できませんでした。\n\n"
                    f"Claude の応答:\n{result['text'][:500]}"
                ),
            }

    async def _execute_repair(self, diagnosis: dict) -> dict:
        """修復フェーズの実行（Phase 4: Git safety 付き）。"""
        actions_taken = []
        now = datetime.now(JST)
        branch_name = f"repair/{now.strftime('%Y%m%d_%H%M%S')}"

        try:
            # Git: 修復ブランチ作成
            git_ok = self._git_create_repair_branch(branch_name)
            if git_ok:
                actions_taken.append(f"Gitブランチ作成: {branch_name}")

            # 修復プロンプト
            fixes = diagnosis.get("proposed_fixes", [])
            fix_descriptions = "\n".join(
                f"- {f['file']}: {f['description']}" for f in fixes
            )

            repair_prompt = (
                f"以下の修正を実行してください:\n\n{fix_descriptions}\n\n"
                f"修正後、変更したファイルのパスを報告してください。"
            )

            result = await self.claude.ask(
                repair_prompt,
                system_prompt=REPAIR_SYSTEM_PROMPT,
                profile="repair",
                max_turns=15,
            )

            cost = result.get("cost_usd", 0)
            self._repair_cost_today += cost

            if not result["success"]:
                # 修復失敗: ロールバック
                if git_ok:
                    self._git_rollback(branch_name)
                    actions_taken.append("Gitロールバック実行")
                return {
                    "attempted": True,
                    "success": False,
                    "actions_taken": actions_taken,
                    "message": f"⚠️ 修復中にエラー: {result['error']}",
                }

            # 構文チェック
            syntax_ok = self._check_syntax()
            actions_taken.append(
                f"構文チェック: {'✅ 成功' if syntax_ok else '❌ 失敗'}"
            )

            if not syntax_ok:
                if git_ok:
                    self._git_rollback(branch_name)
                    actions_taken.append("構文エラーのためロールバック")
                return {
                    "attempted": True,
                    "success": False,
                    "actions_taken": actions_taken,
                    "message": "❌ 修復後の構文チェックに失敗。ロールバックしました。",
                }

            # Git: コミット
            if git_ok:
                summary = diagnosis.get("summary", "self-repair")
                self._git_commit_repair(summary)
                actions_taken.append(f"Git commit: self-repair: {summary[:50]}")

            # 再起動が必要な場合
            if diagnosis.get("needs_restart", False):
                # 修復状態を保存（再起動後に検証するため）
                health = getattr(self.bot, 'health_monitor', None)
                if health:
                    health.set_repair_state({
                        "branch": branch_name,
                        "description": diagnosis.get("summary", ""),
                        "timestamp": now.isoformat(),
                        "original_error": diagnosis.get("diagnosis", ""),
                    })
                actions_taken.append("再起動が必要です（/restart で実行）")

            return {
                "attempted": True,
                "success": True,
                "actions_taken": actions_taken,
                "message": (
                    f"✅ **自己修復完了**\n\n"
                    f"**実行内容:**\n" +
                    "\n".join(f"  • {a}" for a in actions_taken) +
                    f"\n\n💰 修復コスト: ${cost:.4f}" +
                    (f"\n\n⚡ 変更を反映するには `/restart` を実行してください。"
                     if diagnosis.get("needs_restart") else "")
                ),
            }

        except Exception as e:
            logger.error(f"修復実行エラー: {e}", exc_info=True)
            # ロールバック試行
            try:
                self._git_rollback(branch_name)
                actions_taken.append("例外発生のためロールバック")
            except Exception:
                pass
            return {
                "attempted": True,
                "success": False,
                "actions_taken": actions_taken,
                "message": f"❌ 修復中に予期しないエラー: {e}",
            }

    def _parse_diagnosis(self, text: str) -> dict:
        """Claude応答からJSON診断結果を抽出。"""
        # ```json ... ``` ブロックを探す
        import re
        json_match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 直接JSONパースを試す
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # { から } までを抽出
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _get_log_tail(self, lines: int = 50) -> str:
        """ログファイルの末尾を取得。"""
        log_file = LOGS_DIR / "lex.log"
        try:
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                    return "".join(all_lines[-lines:])
        except Exception as e:
            logger.debug(f"ログ読み込みエラー: {e}")
        return ""

    def _check_cooldown(self) -> bool:
        """クールダウンチェック。"""
        if self._last_repair_attempt is None:
            return True
        elapsed = (datetime.now(JST) - self._last_repair_attempt).total_seconds()
        return elapsed >= REPAIR_COOLDOWN_SECONDS

    def _check_budget(self) -> bool:
        """予算チェック。日付変更でリセット。"""
        today = datetime.now(JST).date()
        if today != self._last_reset_date:
            self._repair_attempts_today = 0
            self._repair_cost_today = 0.0
            self._last_reset_date = today

        if self._repair_cost_today >= MAX_REPAIR_COST_PER_DAY:
            return False
        if self._repair_attempts_today >= MAX_REPAIR_ATTEMPTS_PER_HOUR:
            return False
        return True

    # --- Git操作 ---

    def _git_create_repair_branch(self, branch_name: str) -> bool:
        """修復用ブランチを作成。"""
        try:
            subprocess.run(
                ["git", "stash"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            result = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"修復ブランチ作成: {branch_name}")
                return True
            logger.warning(f"修復ブランチ作成失敗: {result.stderr.decode()}")
        except Exception as e:
            logger.warning(f"Git操作エラー: {e}")
        return False

    def _git_commit_repair(self, message: str):
        """修復内容をコミット。"""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", f"self-repair: {message}"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            logger.info(f"修復コミット作成: {message[:50]}")
        except Exception as e:
            logger.warning(f"Gitコミットエラー: {e}")

    def _git_rollback(self, branch_name: str):
        """修復ブランチを破棄してmainに戻る。"""
        try:
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            logger.info(f"ロールバック完了: {branch_name}")
        except Exception as e:
            logger.warning(f"ロールバックエラー: {e}")

    def _check_syntax(self) -> bool:
        """変更されたPythonファイルの構文チェック。"""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
            )
            changed_files = result.stdout.decode().strip().split("\n")
            py_files = [f for f in changed_files if f.endswith(".py")]

            for py_file in py_files:
                file_path = PROJECT_ROOT / py_file
                if file_path.exists():
                    check = subprocess.run(
                        ["python3", "-m", "py_compile", str(file_path)],
                        capture_output=True, timeout=10,
                    )
                    if check.returncode != 0:
                        logger.error(
                            f"構文エラー: {py_file}: "
                            f"{check.stderr.decode()[:200]}"
                        )
                        return False
            return True
        except Exception as e:
            logger.warning(f"構文チェックエラー: {e}")
            return False
