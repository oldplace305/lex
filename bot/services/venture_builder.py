"""Venture構築パイプライン。
承認されたVentureのコードをClaude CLIで生成し、デプロイを試みる。

フロー:
1. プロジェクトディレクトリ作成 (data/venture_projects/V001/)
2. Claude CLI (ventureプロファイル: 900s, 30 turns) でコード生成
3. Vercel CLIでデプロイ（利用可能な場合）
4. 結果をDiscordに報告
"""
import asyncio
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from bot.services.claude_cli import ClaudeCLIBridge
from bot.utils.paths import VENTURES_PROJECTS_DIR

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# 構築プロンプト
BUILD_PROMPT = """
あなたはLex Ventures のエンジニア。
以下のVentureアイデアを実装してください。

## Venture情報
- 名前: {name}
- 概要: {description}
- 収益化: {monetization}
- 難易度: {difficulty}

## 要件
1. **最小限の動くバージョン（MVP）** を作る。完璧より先にシップ。
2. 静的サイト（HTML/CSS/JS）またはNext.js/Astro等のシンプルな構成。
3. Vercel or Cloudflare Pagesで無料デプロイ可能な構成。
4. 全ファイルを {project_dir} に作成する。
5. README.mdに概要・デプロイ手順を含める。
6. 日本語UI。

## 出力
コードを書き終えたら、以下のJSON形式で結果を報告:
```json
{{
    "status": "success" or "partial",
    "files_created": ["ファイルパスのリスト"],
    "summary": "何を作ったかの要約（日本語1-2文）",
    "deploy_ready": true or false,
    "deploy_command": "デプロイコマンド（例: npx vercel --yes）"
}}
```
""".strip()


class VentureBuilder:
    """Ventureの自動構築を管理するクラス。"""

    def __init__(self, health_monitor=None):
        self.claude = ClaudeCLIBridge(health_monitor=health_monitor)
        VENTURES_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    async def build(self, vid: str, venture: dict) -> dict:
        """Ventureのコードを生成する。

        Args:
            vid: Venture ID (例: "V001")
            venture: Ventureデータ辞書

        Returns:
            dict: {
                "success": bool,
                "summary": str,
                "project_dir": str,
                "url": str or None,
                "error": str or None,
            }
        """
        project_dir = VENTURES_PROJECTS_DIR / vid
        project_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"🔨 Venture構築開始: {vid} → {project_dir}")

        # Step 1: Claude CLIでコード生成
        build_result = await self._generate_code(vid, venture, project_dir)
        if not build_result["success"]:
            return build_result

        # Step 2: デプロイ試行
        deploy_url = await self._try_deploy(vid, project_dir)

        summary = build_result.get("summary", "構築完了")
        return {
            "success": True,
            "summary": summary,
            "project_dir": str(project_dir),
            "url": deploy_url,
            "error": None,
        }

    async def _generate_code(self, vid: str, venture: dict,
                             project_dir: Path) -> dict:
        """Claude CLIを使ってコードを生成。"""
        prompt = BUILD_PROMPT.format(
            name=venture.get("name", "無題"),
            description=venture.get("description", ""),
            monetization=venture.get("monetization", ""),
            difficulty=venture.get("difficulty", "medium"),
            project_dir=str(project_dir),
        )

        # ventureプロファイル: 900s timeout, 30 max_turns
        # ファイル操作ツールを許可
        result = await self.claude.ask(
            prompt,
            profile="venture",
            allowed_tools=[
                "Write",
                "Edit",
                "Read",
                "Bash(mkdir:*)",
                "Bash(ls:*)",
                "Bash(cat:*)",
                "Bash(npm:*)",
                "Bash(npx:*)",
            ],
        )

        if not result["success"]:
            logger.error(f"🔨 Venture構築失敗: {vid} - {result['error']}")
            return {
                "success": False,
                "summary": "",
                "project_dir": str(project_dir),
                "url": None,
                "error": result["error"],
            }

        # 生成されたファイル確認
        files = list(project_dir.rglob("*"))
        files = [f for f in files if f.is_file()]

        if not files:
            logger.warning(f"🔨 Venture構築: ファイルが生成されませんでした: {vid}")
            return {
                "success": False,
                "summary": "",
                "project_dir": str(project_dir),
                "url": None,
                "error": "ファイルが生成されませんでした",
            }

        logger.info(f"🔨 Venture構築完了: {vid} ({len(files)}ファイル)")

        # Claude応答から要約を抽出
        summary = self._extract_summary(result["text"])

        return {
            "success": True,
            "summary": summary,
            "project_dir": str(project_dir),
            "url": None,
            "error": None,
            "files_count": len(files),
        }

    def _extract_summary(self, text: str) -> str:
        """Claude応答から要約を抽出。"""
        import json
        import re

        # JSON部分を探す
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return data.get("summary", "構築完了")
            except json.JSONDecodeError:
                pass

        # JSONがなければ最初の200文字を要約として使う
        clean = text.strip()
        if len(clean) > 200:
            clean = clean[:200] + "..."
        return clean if clean else "構築完了"

    async def _try_deploy(self, vid: str, project_dir: Path) -> Optional[str]:
        """Vercel CLIでデプロイを試みる。

        Returns:
            str: デプロイURL or None
        """
        # Vercel CLIが利用可能かチェック
        vercel_available = await self._check_command("npx vercel --version")
        if not vercel_available:
            logger.info(f"🔨 Vercel CLI未インストール。デプロイスキップ: {vid}")
            return None

        logger.info(f"🚀 Vercelデプロイ開始: {vid}")

        try:
            import os
            env = os.environ.copy()
            env["HOME"] = "/Users/shuta"
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

            process = await asyncio.create_subprocess_exec(
                "npx", "vercel", "--yes", "--prod",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_dir),
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=120,
            )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if process.returncode == 0 and stdout_text:
                # Vercelは成功時にURLを出力する
                url = self._extract_url(stdout_text)
                if url:
                    logger.info(f"🚀 デプロイ成功: {vid} → {url}")
                    return url

            logger.warning(
                f"🚀 デプロイ失敗: {vid} "
                f"(rc={process.returncode}, stderr={stderr_text[:200]})"
            )
            return None

        except asyncio.TimeoutError:
            logger.error(f"🚀 デプロイタイムアウト: {vid}")
            return None
        except Exception as e:
            logger.error(f"🚀 デプロイエラー: {vid} - {e}")
            return None

    async def _check_command(self, command: str) -> bool:
        """コマンドが利用可能かチェック。"""
        try:
            import os
            env = os.environ.copy()
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(process.communicate(), timeout=15)
            return process.returncode == 0
        except Exception:
            return False

    def _extract_url(self, text: str) -> Optional[str]:
        """テキストからURLを抽出。"""
        import re
        # https://xxx.vercel.app パターン
        url_match = re.search(r"https://[\w.-]+\.vercel\.app\S*", text)
        if url_match:
            return url_match.group(0)
        # 汎用HTTPSパターン
        url_match = re.search(r"https://\S+", text)
        if url_match:
            return url_match.group(0)
        return None

    def get_project_dir(self, vid: str) -> Path:
        """Ventureのプロジェクトディレクトリを返す。"""
        return VENTURES_PROJECTS_DIR / vid

    def list_project_files(self, vid: str) -> list:
        """Ventureプロジェクトのファイル一覧を返す。"""
        project_dir = self.get_project_dir(vid)
        if not project_dir.exists():
            return []
        files = list(project_dir.rglob("*"))
        return [str(f.relative_to(project_dir)) for f in files if f.is_file()]
