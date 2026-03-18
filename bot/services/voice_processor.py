"""音声自動処理サービス。
音声入力テキストをClaude CLIで意図判定・リライトし、
結果に応じてApple Notes保存やDiscord通知を行う。
"""
import json
import logging
import re
from typing import Callable, Awaitable

from bot.services.claude_cli import ClaudeCLIBridge
from bot.services.apple_notes import AppleNotesService
from bot.services.voice_prompt import VOICE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class VoiceProcessor:
    """音声入力テキストの自動処理パイプライン。

    フロー:
    1. Claude CLIで意図判定 + リライト
    2. JSON解析
    3. タスク種別に応じた処理（Notes保存、リサーチ、Discord通知）
    """

    def __init__(
        self,
        claude_bridge: ClaudeCLIBridge,
        notes_service: AppleNotesService,
        notify_func: Callable[[str], Awaitable[None]],
    ):
        self.claude = claude_bridge
        self.notes = notes_service
        self.notify = notify_func

    async def process(self, raw_voice_text: str) -> dict:
        """音声テキストを処理する全フロー。

        Args:
            raw_voice_text: Siri等で文字起こしされた音声テキスト

        Returns:
            dict: 処理結果（task_type, note_name, raw_text, rewritten_text等）
        """
        logger.info(f"音声処理開始: {raw_voice_text[:80]}...")

        # 1. Claude CLIで意図判定 + リライト
        result = await self.claude.ask(
            prompt=raw_voice_text,
            system_prompt=VOICE_SYSTEM_PROMPT,
            profile="complex",  # リライトは時間がかかるため
            max_turns=1,        # 1ターンで完結（ツール使用なし）
        )

        if not result["success"]:
            error_msg = result.get("error", "不明なエラー")
            logger.error(f"Claude CLI呼び出し失敗: {error_msg}")
            # エラー時はDiscordに通知して原文をそのままメモ保存
            await self._fallback_save(raw_voice_text, error_msg)
            return {
                "task_type": "error",
                "error": error_msg,
                "raw_voice_text": raw_voice_text,
            }

        # 2. JSON解析
        parsed = self._parse_response(result["text"])
        logger.info(f"タスク判定: {parsed['task_type']}")

        # 3. タスク種別に応じた処理
        try:
            await self._dispatch(parsed, raw_voice_text)
        except Exception as e:
            logger.error(f"処理中にエラー: {e}", exc_info=True)
            await self.notify(f"⚠️ 音声処理中にエラー: {e}")

        return parsed

    async def _dispatch(self, parsed: dict, raw_voice_text: str):
        """タスク種別に応じた処理を実行。"""
        task_type = parsed["task_type"]

        if task_type in ("x_post", "note_article", "memo"):
            # Apple Notesに保存
            note_name = parsed.get("note_name", "メモ")
            raw_text = parsed.get("raw_text", raw_voice_text)
            rewritten_text = parsed.get("rewritten_text", "")

            save_result = await self.notes.append_to_note(
                note_name, raw_text, rewritten_text
            )

            if save_result["success"]:
                # Discord通知（要約 + 確認事項）
                summary = parsed.get("discord_summary", raw_text[:100])
                msg = f"📝 **{note_name}** に記録しました\n> {summary}"
                warnings = parsed.get("warnings")
                if warnings:
                    msg += f"\n\n⚠️ {warnings}"
                await self.notify(msg)
            else:
                await self.notify(
                    f"⚠️ メモ保存失敗: {save_result['error']}\n"
                    f"原文: {raw_text[:200]}"
                )

        elif task_type == "research":
            query = parsed.get("research_query", raw_voice_text)
            await self.notify(f"🔍 **リサーチ開始**: {query}")

            # Claude CLIでリサーチ実行
            research_result = await self.claude.ask(
                prompt=f"以下について調べて、さかな🐟の口調で簡潔にまとめてください:\n{query}",
                profile="complex",
            )

            if research_result["success"]:
                # リサーチ結果をDiscord + メモに保存
                result_text = research_result["text"]
                await self.notify(
                    f"✅ **リサーチ完了**: {query}\n\n"
                    f"{result_text[:1500]}"
                    f"{'...(続きあり)' if len(result_text) > 1500 else ''}"
                )
                # メモにも保存
                await self.notes.append_to_note(
                    "メモ", f"リサーチ: {query}", result_text
                )
            else:
                await self.notify(
                    f"⚠️ リサーチ失敗: {research_result.get('error', '不明')}"
                )

        elif task_type == "unknown":
            # 判定不能 → Discordで確認を求める
            await self.notify(
                f"❓ タスク種別を判定できませんでした\n"
                f"> {raw_voice_text[:200]}\n\n"
                f"ポスト？ ノート？ メモ？ 調べもの？"
            )

        else:
            # 想定外のtask_type
            await self.notify(
                f"⚠️ 不明なタスク種別: {task_type}\n"
                f"> {raw_voice_text[:200]}"
            )

    async def _fallback_save(self, raw_voice_text: str, error_msg: str):
        """エラー時のフォールバック: 原文をそのままメモに保存。"""
        await self.notify(
            f"⚠️ Claude処理に失敗しました: {error_msg}\n"
            f"原文をメモに保存します"
        )
        try:
            await self.notes.append_to_note(
                "メモ", raw_voice_text, "(Claude処理失敗・原文のみ保存)"
            )
            await self.notify(f"📝 **メモ** に原文を保存しました")
        except Exception as e:
            logger.error(f"フォールバック保存も失敗: {e}")
            await self.notify(f"⚠️ フォールバック保存も失敗: {e}")

    def _parse_response(self, response_text: str) -> dict:
        """Claude CLIの応答テキストからJSONを抽出・解析する。

        Claude CLIは時々JSONの前後に説明テキストを入れることがあるため、
        JSON部分だけを抽出する。
        """
        parsed = None

        # まず直接JSONパースを試みる
        try:
            parsed = json.loads(response_text.strip())
        except json.JSONDecodeError:
            pass

        # コードブロック内のJSONを探す（```json ... ``` パターン）
        if parsed is None:
            code_block_match = re.search(
                r'```(?:json)?\s*\n?(.*?)\n?```',
                response_text,
                re.DOTALL,
            )
            if code_block_match:
                try:
                    parsed = json.loads(code_block_match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # { から } までの最大範囲を探す
        if parsed is None:
            brace_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if brace_match:
                try:
                    parsed = json.loads(brace_match.group(0))
                except json.JSONDecodeError:
                    pass

        # どれも失敗した場合はフォールバック
        if parsed is None:
            logger.warning(f"JSONパース失敗。フォールバック処理: {response_text[:200]}")
            return {
                "task_type": "memo",
                "note_name": "メモ",
                "raw_text": response_text,
                "rewritten_text": "(JSON解析失敗・原文保存)",
                "discord_summary": "JSON解析に失敗したため、原文をメモに保存しました",
                "research_query": None,
                "warnings": None,
            }

        # 各フィールドを文字列に正規化（dictやlistが入っている場合がある）
        return self._normalize_fields(parsed)

    def _normalize_fields(self, parsed: dict) -> dict:
        """JSONフィールドを文字列に正規化する。

        Claude CLIがrewritten_textをdictやlistで返すことがあるため、
        すべてのテキストフィールドを文字列に変換する。
        """
        str_fields = ["raw_text", "rewritten_text", "discord_summary",
                       "research_query", "warnings"]
        for field in str_fields:
            value = parsed.get(field)
            if value is None:
                continue
            if isinstance(value, dict):
                # dictの場合はJSON風に整形して文字列化
                parts = []
                for k, v in value.items():
                    if isinstance(v, str):
                        parts.append(f"【{k}】\n{v}")
                    else:
                        parts.append(f"【{k}】\n{json.dumps(v, ensure_ascii=False)}")
                parsed[field] = "\n\n".join(parts)
            elif isinstance(value, list):
                # listの場合は改行で結合
                str_items = []
                for item in value:
                    if isinstance(item, str):
                        str_items.append(item)
                    else:
                        str_items.append(json.dumps(item, ensure_ascii=False))
                parsed[field] = "\n".join(str_items)
            elif not isinstance(value, str):
                parsed[field] = str(value)

        return parsed
