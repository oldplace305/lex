"""Claude Code CLI連携モジュール。
subprocessでClaude Code CLIを非同期呼び出しし、JSON形式で結果を取得する。
"""
import asyncio
import json
import logging
import os
from bot.utils.paths import NODE_BIN, CLAUDE_CLI, PROJECT_ROOT
from bot.config import CLAUDE_OAUTH_TOKEN

logger = logging.getLogger(__name__)


class ClaudeCLIBridge:
    """Claude Code CLIとの通信を管理するクラス。

    - asyncio.create_subprocess_execで非同期実行
    - --output-format json でJSON出力を取得
    - -p フラグで非インタラクティブモード
    - asyncio.Lockで同時実行を防止
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self._lock = asyncio.Lock()  # 同時に1つだけ実行

    async def ask(self, prompt: str, system_prompt: str = None,
                 allowed_tools: list = None, max_turns: int = None) -> dict:
        """Claude Code CLIにプロンプトを送信し、結果を返す。

        Args:
            prompt: ユーザーからの質問・指示
            system_prompt: システムプロンプト（オーナープロフィール等）
            allowed_tools: 許可するツールのリスト（リスクレベルに応じた制御）
            max_turns: 最大ターン数（None=デフォルト3）

        Returns:
            dict: {"success": bool, "text": str, "error": str or None}
        """
        async with self._lock:
            return await self._execute(prompt, system_prompt, allowed_tools, max_turns)

    async def _execute(self, prompt: str, system_prompt: str = None,
                       allowed_tools: list = None, max_turns: int = None) -> dict:
        """実際のCLI実行処理。"""
        # デフォルトのmax-turnsは3（普通の会話は1で十分、余裕を持たせて3）
        turns = str(max_turns) if max_turns else "3"

        # コマンド構築
        cmd = [
            NODE_BIN, CLAUDE_CLI,
            "-p", prompt,                    # 非インタラクティブ
            "--output-format", "json",       # JSON出力
            "--max-turns", turns,             # 自律実行の最大ターン数
        ]

        # 許可ツール制御（スマート承認システム連携）
        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowedTools", tool])

        # システムプロンプト追加
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        logger.info(f"Claude CLI呼び出し開始（タイムアウト: {self.timeout}秒）")
        logger.debug(f"プロンプト: {prompt[:100]}...")

        try:
            # 環境変数を明示的に設定
            # CLAUDECODE環境変数を除去して入れ子ブロックを回避
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            env["HOME"] = "/Users/shuta"
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

            # Claude CLI認証トークンを設定（launchd環境で必要）
            if CLAUDE_OAUTH_TOKEN:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_OAUTH_TOKEN

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(PROJECT_ROOT),  # プロジェクトルートで実行
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            if process.returncode != 0:
                logger.warning(
                    f"Claude CLI 非ゼロ終了 (code={process.returncode}): "
                    f"stderr={stderr_text[:200]}"
                )
                logger.warning(f"stdout内容: {stdout_text[:300]}")
                # "Not logged in" チェック
                if "not logged in" in stdout_text.lower() or "please run /login" in stdout_text.lower():
                    logger.error("Claude CLIが未ログイン状態です")
                    return {
                        "success": False,
                        "text": "",
                        "error": "Claude CLIが未ログイン状態です。Mac miniのターミナルで `claude login` を実行してください。"
                    }
                # stdoutに応答がある場合は成功として扱う
                # Claude CLI v2はreturncode=1でもstdoutに結果を出力する場合がある
                if stdout_text.strip():
                    logger.info("stdoutに応答あり。成功として処理します")
                else:
                    logger.error("stdoutが空。エラーとして処理します")
                    return {
                        "success": False,
                        "text": "",
                        "error": f"CLI error: {stderr_text[:500] if stderr_text.strip() else '応答がありませんでした'}"
                    }

            # JSON出力をパース
            response_text = self._extract_text(stdout_text)
            cost_usd = self._extract_cost(stdout_text)
            logger.info(
                f"Claude CLI応答取得成功（{len(response_text)}文字, "
                f"コスト: ${cost_usd:.4f}）"
            )

            return {
                "success": True,
                "text": response_text,
                "error": None,
                "cost_usd": cost_usd,
            }

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI タイムアウト（{self.timeout}秒）")
            # タイムアウト時はプロセスを強制終了
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return {
                "success": False,
                "text": "",
                "error": f"タイムアウト（{self.timeout}秒を超過しました）"
            }

        except Exception as e:
            logger.error(f"Claude CLI 予期しないエラー: {e}", exc_info=True)
            return {
                "success": False,
                "text": "",
                "error": f"予期しないエラー: {str(e)}"
            }

    def _extract_text(self, raw_output: str) -> str:
        """Claude CLI のJSON出力からテキストを抽出する。

        --output-format json の出力形式:
        {
            "type": "result",
            "subtype": "success" | "error_max_turns" | ...,
            "result": "テキスト応答" | null,
            "total_cost_usd": 0.03,
            ...
        }
        """
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError:
            # JSONパースに失敗した場合、生テキストをそのまま返す
            logger.warning("JSON解析失敗。生テキストを返します")
            return raw_output.strip()

        if not isinstance(data, dict):
            # リスト形式（content blocks直接）
            if isinstance(data, list):
                return self._extract_from_blocks(data)
            logger.warning("未知のJSON構造")
            return str(data)

        # --- メインパターン: Claude CLI --output-format json ---
        # {"type": "result", "subtype": "success|error_max_turns", "result": "..." | null}
        subtype = data.get("subtype", "")
        result = data.get("result")

        # error_max_turns: ターン上限に達した場合
        if subtype == "error_max_turns":
            logger.warning("Claude CLIがmax-turnsに到達しました")
            if result and isinstance(result, str) and result.strip():
                return result
            # result が null の場合 → ツール使用中に打ち切られた
            return "⚠️ 処理が複雑すぎてターン上限に達しました。もう少し簡潔に質問してみてください。"

        # result フィールドがある場合
        if result is not None:
            if isinstance(result, str) and result.strip():
                return result
            if isinstance(result, list):
                return self._extract_from_blocks(result)

        # result が None/空 でも subtype が success の場合
        if subtype == "success" and (result is None or result == ""):
            logger.warning("success だが result が空")
            return "（応答が空でした。もう一度お試しください。）"

        # パターン2: {"content": "テキスト"} 形式（古い形式の互換）
        if "content" in data:
            content = data["content"]
            if isinstance(content, str) and content.strip():
                return content

        # is_error フラグチェック
        if data.get("is_error"):
            error_msg = result or data.get("error", "不明なエラー")
            return f"⚠️ Claude CLIエラー: {error_msg}"

        # フォールバック: 有用な情報だけ返す（JSON生データは返さない）
        logger.warning(f"未知のレスポンス構造: subtype={subtype}, result_type={type(result).__name__}")
        return "⚠️ 予期しない応答形式です。もう一度お試しください。"

    def _extract_cost(self, raw_output: str) -> float:
        """Claude CLI のJSON出力からコスト情報を抽出。

        Returns:
            float: APIコスト（USD）。取得できない場合は0。
        """
        try:
            data = json.loads(raw_output)
            if isinstance(data, dict):
                # total_cost_usd フィールド
                cost = data.get("total_cost_usd", 0)
                if cost:
                    return float(cost)
                # usage内のcostUSD
                usage = data.get("usage", {})
                if isinstance(usage, dict):
                    return 0
                # modelUsage内のcostUSD
                model_usage = data.get("modelUsage", {})
                if isinstance(model_usage, dict):
                    for model_info in model_usage.values():
                        if isinstance(model_info, dict):
                            cost = model_info.get("costUSD", 0)
                            if cost:
                                return float(cost)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return 0

    def _extract_from_blocks(self, blocks: list) -> str:
        """コンテントブロックのリストからテキストを抽出。"""
        texts = []
        for block in blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif "content" in block:
                    texts.append(str(block["content"]))
                elif "text" in block:
                    texts.append(str(block["text"]))
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts) if texts else str(blocks)
