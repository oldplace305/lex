"""スクリプト管理モジュール（Script Manager）。
登録されたスクリプトの管理・実行・結果通知を行う。

scripts.json でスクリプトを管理し、
Discordから /run コマンドで実行できるようにする。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot.utils.paths import DATA_DIR, PYTHON_BIN
from bot.config import CLAUDE_OAUTH_TOKEN

logger = logging.getLogger(__name__)

# スクリプト定義ファイル
SCRIPTS_FILE = DATA_DIR / "scripts.json"

# 日本時間
JST = timezone(timedelta(hours=9))

# スクリプト実行のデフォルトタイムアウト（秒）
DEFAULT_TIMEOUT = 300  # 5分


class ScriptResult:
    """スクリプト実行結果を表すクラス。"""

    def __init__(self, script_id: str, success: bool,
                 stdout: str = "", stderr: str = "",
                 return_code: int = 0, duration_sec: float = 0,
                 error: str = ""):
        self.script_id = script_id
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.duration_sec = duration_sec
        self.error = error

    def summary(self, max_length: int = 500) -> str:
        """結果のサマリーを生成。"""
        if self.success:
            output = self.stdout.strip()
            if len(output) > max_length:
                output = output[:max_length] + "\n... (省略)"
            return output if output else "(出力なし)"
        else:
            err = self.stderr.strip() or self.error
            if len(err) > max_length:
                err = err[:max_length] + "\n... (省略)"
            return err if err else f"終了コード: {self.return_code}"


class ScriptManager:
    """スクリプトの登録・管理・実行を行うクラス。"""

    def __init__(self):
        self._scripts = self._load_scripts()

    def _load_scripts(self) -> dict:
        """scripts.jsonを読み込む。"""
        if SCRIPTS_FILE.exists():
            try:
                with open(SCRIPTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"scripts.json読み込みエラー: {e}")

        # 初回：デフォルトの空設定を作成
        default = {
            "version": 1,
            "scripts": [],
        }
        self._save_scripts(default)
        return default

    def _save_scripts(self, data: dict):
        """scripts.jsonを保存。"""
        SCRIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCRIPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list_scripts(self) -> list:
        """登録済みスクリプト一覧を取得。"""
        return self._scripts.get("scripts", [])

    def get_script(self, script_id: str) -> dict:
        """スクリプトIDで検索。

        Args:
            script_id: スクリプトID

        Returns:
            dict or None: スクリプト情報
        """
        for script in self._scripts.get("scripts", []):
            if script.get("id") == script_id:
                return script
        return None

    def add_script(self, script_id: str, name: str, command: str,
                   workdir: str = "", risk_level: str = "MEDIUM",
                   description: str = "", timeout: int = DEFAULT_TIMEOUT) -> bool:
        """スクリプトを登録。

        Args:
            script_id: 一意なID（英数字+アンダースコア）
            name: 表示名（日本語OK）
            command: 実行コマンド
            workdir: 作業ディレクトリ（省略時はプロジェクトルート）
            risk_level: リスクレベル（LOW/MEDIUM/HIGH）
            description: 説明
            timeout: タイムアウト（秒）

        Returns:
            bool: 成功/失敗
        """
        # 重複チェック
        if self.get_script(script_id):
            logger.warning(f"スクリプトID '{script_id}' は既に登録済みです")
            return False

        entry = {
            "id": script_id,
            "name": name,
            "command": command,
            "workdir": workdir,
            "risk_level": risk_level,
            "description": description,
            "timeout": timeout,
            "schedule": None,
            "last_run": None,
            "last_status": None,
            "created_at": datetime.now(JST).isoformat(),
        }

        self._scripts.setdefault("scripts", []).append(entry)
        self._save_scripts(self._scripts)
        logger.info(f"スクリプト登録: {script_id} ({name})")
        return True

    def remove_script(self, script_id: str) -> bool:
        """スクリプトを削除。"""
        scripts = self._scripts.get("scripts", [])
        new_list = [s for s in scripts if s.get("id") != script_id]

        if len(new_list) == len(scripts):
            return False

        self._scripts["scripts"] = new_list
        self._save_scripts(self._scripts)
        logger.info(f"スクリプト削除: {script_id}")
        return True

    async def run_script(self, script_id: str) -> ScriptResult:
        """スクリプトを非同期で実行。

        Args:
            script_id: 実行するスクリプトのID

        Returns:
            ScriptResult: 実行結果
        """
        script = self.get_script(script_id)
        if not script:
            return ScriptResult(
                script_id=script_id,
                success=False,
                error=f"スクリプト '{script_id}' が見つかりません",
            )

        command = script.get("command", "")
        workdir = script.get("workdir", "")
        timeout = script.get("timeout", DEFAULT_TIMEOUT)

        if not command:
            return ScriptResult(
                script_id=script_id,
                success=False,
                error="実行コマンドが設定されていません",
            )

        logger.info(f"スクリプト実行開始: {script_id} ({command})")
        start_time = datetime.now(JST)

        try:
            # 環境変数を設定
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            env["HOME"] = "/Users/shuta"
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
            if CLAUDE_OAUTH_TOKEN:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_OAUTH_TOKEN

            # venv内のPythonを使う場合のPATH追加
            # venv内のPythonを使う場合のPATH追加（動的解決）
            from bot.utils.paths import PROJECT_ROOT as _pr
            env["PATH"] = f"{_pr / 'venv' / 'bin'}:{env['PATH']}"

            # 作業ディレクトリの解決
            cwd = workdir if workdir and Path(workdir).exists() else None

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            duration = (datetime.now(JST) - start_time).total_seconds()
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            success = process.returncode == 0

            result = ScriptResult(
                script_id=script_id,
                success=success,
                stdout=stdout_text,
                stderr=stderr_text,
                return_code=process.returncode,
                duration_sec=round(duration, 1),
            )

            # 実行記録を更新
            self._update_last_run(script_id, success, duration)

            logger.info(
                f"スクリプト実行完了: {script_id} "
                f"(成功={success}, {duration:.1f}秒)"
            )
            return result

        except asyncio.TimeoutError:
            duration = (datetime.now(JST) - start_time).total_seconds()
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass

            self._update_last_run(script_id, False, duration)
            logger.error(f"スクリプトタイムアウト: {script_id} ({timeout}秒)")

            return ScriptResult(
                script_id=script_id,
                success=False,
                error=f"タイムアウト（{timeout}秒を超過）",
                duration_sec=round(duration, 1),
            )

        except Exception as e:
            duration = (datetime.now(JST) - start_time).total_seconds()
            self._update_last_run(script_id, False, duration)
            logger.error(f"スクリプト実行エラー: {script_id}: {e}")

            return ScriptResult(
                script_id=script_id,
                success=False,
                error=str(e),
                duration_sec=round(duration, 1),
            )

    def _update_last_run(self, script_id: str, success: bool,
                         duration: float):
        """スクリプトの最終実行情報を更新。"""
        for script in self._scripts.get("scripts", []):
            if script.get("id") == script_id:
                script["last_run"] = datetime.now(JST).isoformat()
                script["last_status"] = "success" if success else "failed"
                script["last_duration"] = round(duration, 1)
                break
        self._save_scripts(self._scripts)
