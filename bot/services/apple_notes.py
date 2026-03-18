"""AppleScript経由でAppleメモに追記するサービス。
音声入力ワークフロー Phase 2 の中核コンポーネント。

使い方:
  service = AppleNotesService()
  result = await service.append_to_note("X投稿案", "原文テキスト", "リライトテキスト")
"""
import asyncio
import subprocess
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# 許可されたノート名（ホワイトリスト）
VALID_NOTE_NAMES = {"X投稿案", "Note投稿案", "メモ"}


class AppleNotesService:
    """AppleメモへのAppleScript追記サービス。"""

    async def append_to_note(
        self, note_name: str, raw_text: str, rewritten_text: str
    ) -> dict:
        """Appleメモに原文+リライトを追記する。

        Args:
            note_name: ノート名（"X投稿案" / "Note投稿案" / "メモ"）
            raw_text: 音声入力の原文
            rewritten_text: Claude.aiでリライトされたテキスト

        Returns:
            {"success": True/False, "note_name": str, "error": str or None}
        """
        if note_name not in VALID_NOTE_NAMES:
            return {
                "success": False,
                "note_name": note_name,
                "error": f"無効なノート名: {note_name}（許可: {', '.join(VALID_NOTE_NAMES)}）",
            }

        # 追記用HTMLを組み立て
        now = datetime.now(JST)
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        html_block = self._build_html(timestamp, raw_text, rewritten_text)

        # AppleScriptをブロッキングせずに実行
        try:
            result = await asyncio.to_thread(
                self._run_applescript, note_name, html_block
            )
            return result
        except Exception as e:
            logger.error(f"AppleScript実行エラー: {e}", exc_info=True)
            return {
                "success": False,
                "note_name": note_name,
                "error": str(e),
            }

    def _build_html(
        self, timestamp: str, raw_text: str, rewritten_text: str
    ) -> str:
        """追記用のHTMLブロックを組み立て。"""
        # HTMLエスケープ
        raw_escaped = self._escape_html(raw_text)
        rewritten_escaped = self._escape_html(rewritten_text)

        # 改行をHTMLの<br>に変換
        raw_html = raw_escaped.replace("\n", "<br>")
        rewritten_html = rewritten_escaped.replace("\n", "<br>")

        return (
            f"<hr>"
            f"<h3>📝 {timestamp}</h3>"
            f"<p><b>原文（音声入力）</b><br>{raw_html}</p>"
            f"<p><b>🐟 リライト案</b><br>{rewritten_html}</p>"
        )

    def _escape_html(self, text: str) -> str:
        """HTMLエスケープ。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _run_applescript(self, note_name: str, html_block: str) -> dict:
        """osascriptでAppleメモに追記する（同期処理）。

        ノートが存在しなければ新規作成。存在すれば末尾に追記。
        """
        # AppleScript内でのエスケープ（ダブルクォートとバックスラッシュ）
        escaped_html = html_block.replace("\\", "\\\\").replace('"', '\\"')
        escaped_name = note_name.replace("\\", "\\\\").replace('"', '\\"')

        script = f'''
tell application "Notes"
    set targetFolder to folder "Notes" of account "iCloud"
    set noteFound to false
    set newContent to "{escaped_html}"

    -- ノートを検索
    repeat with aNote in notes of targetFolder
        if name of aNote is "{escaped_name}" then
            -- 既存ノートに追記
            set body of aNote to (body of aNote) & newContent
            set noteFound to true
            exit repeat
        end if
    end repeat

    -- ノートが見つからなければ新規作成
    if not noteFound then
        make new note at targetFolder with properties {{name:"{escaped_name}", body:"<h1>{escaped_name}</h1>" & newContent}}
    end if
end tell
return "ok"
'''

        logger.info(f"AppleScript実行: ノート「{note_name}」に追記")

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            logger.error(f"AppleScript失敗: {error_msg}")
            return {
                "success": False,
                "note_name": note_name,
                "error": error_msg,
            }

        logger.info(f"AppleScript成功: ノート「{note_name}」に追記完了")
        return {
            "success": True,
            "note_name": note_name,
            "error": None,
        }
