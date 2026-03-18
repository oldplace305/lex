"""ローカルAPIサーバーCog — 音声入力ワークフロー Phase 2。
aiohttp.web でHTTPサーバーを起動し、外部からのリクエストを受け付ける。

エンドポイント:
  GET  /health   → ヘルスチェック
  POST /memo     → Appleメモに追記 + Discord通知
  POST /notify   → Discordに通知送信
  POST /research → リサーチをキック → 結果をDiscordに送信
"""
import json
import logging
from aiohttp import web
from discord.ext import commands
from bot.config import OWNER_ID, REPORT_CHANNEL_ID, API_PORT
from bot.services.apple_notes import AppleNotesService

logger = logging.getLogger(__name__)


class ApiServer(commands.Cog):
    """ローカルHTTPサーバーを提供するCog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.notes_service = AppleNotesService()
        self.runner = None
        self._setup_app()

    def _setup_app(self):
        """aiohttpアプリケーションとルートを設定。"""
        self.app = web.Application()
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/memo", self.handle_memo)
        self.app.router.add_post("/notify", self.handle_notify)
        self.app.router.add_post("/research", self.handle_research)

    async def cog_load(self):
        """Cog読み込み時にHTTPサーバーを起動。"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", API_PORT)
        await site.start()
        logger.info(f"⚡ APIサーバー起動: http://127.0.0.1:{API_PORT}")

    async def cog_unload(self):
        """Cog解除時にHTTPサーバーを停止。"""
        if self.runner:
            await self.runner.cleanup()
            logger.info("⚡ APIサーバー停止")

    # --- ヘルパー ---

    def _get_report_channel(self):
        """レポート送信先チャンネルを取得。"""
        if REPORT_CHANNEL_ID:
            channel = self.bot.get_channel(REPORT_CHANNEL_ID)
            if channel:
                return channel
        return None

    async def _send_to_owner(self, content: str):
        """オーナーにメッセージを送信（チャンネル or DM）。"""
        channel = self._get_report_channel()
        if channel:
            await channel.send(content)
            return

        try:
            owner = await self.bot.fetch_user(OWNER_ID)
            if owner:
                await owner.send(content)
        except Exception as e:
            logger.error(f"オーナーへのDM送信失敗: {e}")

    def _json_response(self, data: dict, status: int = 200) -> web.Response:
        """JSON レスポンスを返す。"""
        return web.Response(
            text=json.dumps(data, ensure_ascii=False),
            status=status,
            content_type="application/json",
        )

    # --- エンドポイント ---

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health — ヘルスチェック。"""
        return self._json_response({
            "status": "ok",
            "bot_ready": self.bot.is_ready(),
            "latency_ms": round(self.bot.latency * 1000, 1),
        })

    async def handle_memo(self, request: web.Request) -> web.Response:
        """POST /memo — Appleメモに追記 + Discord通知。

        body: {
            "note_name": "X投稿案",
            "raw_text": "原文テキスト",
            "rewritten_text": "リライトテキスト"
        }
        """
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "error": "JSONパースエラー"}, 400
            )

        note_name = body.get("note_name", "")
        raw_text = body.get("raw_text", "")
        rewritten_text = body.get("rewritten_text", "")

        if not note_name or not raw_text:
            return self._json_response(
                {"status": "error", "error": "note_name と raw_text は必須です"}, 400
            )

        # Appleメモに追記
        result = await self.notes_service.append_to_note(
            note_name, raw_text, rewritten_text
        )

        if result["success"]:
            # 成功したらDiscordに自動通知
            await self._send_to_owner(
                f"📝 **{note_name}** に記録しました\n"
                f"> {raw_text[:100]}{'...' if len(raw_text) > 100 else ''}"
            )
            return self._json_response({"status": "ok", "note_name": note_name})
        else:
            return self._json_response(
                {"status": "error", "error": result["error"]}, 500
            )

    async def handle_notify(self, request: web.Request) -> web.Response:
        """POST /notify — Discordに通知を送信。

        body: { "message": "通知テキスト" }
        """
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "error": "JSONパースエラー"}, 400
            )

        message = body.get("message", "")
        if not message:
            return self._json_response(
                {"status": "error", "error": "message は必須です"}, 400
            )

        await self._send_to_owner(message)
        return self._json_response({"status": "ok"})

    async def handle_research(self, request: web.Request) -> web.Response:
        """POST /research — リサーチをキックしてDiscordに結果を送信。

        body: { "query": "検索クエリ" }
        """
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "error": "JSONパースエラー"}, 400
            )

        query = body.get("query", "")
        if not query:
            return self._json_response(
                {"status": "error", "error": "query は必須です"}, 400
            )

        # Research Cogにリサーチを依頼
        research_cog = self.bot.get_cog("Research")
        if not research_cog:
            # Research Cogがない場合はクエリをそのままDiscordに転送
            await self._send_to_owner(
                f"🔍 **リサーチリクエスト**\n> {query}\n\n"
                f"⚠️ Research Cogが未ロードのため、手動で確認してください。"
            )
            return self._json_response({
                "status": "ok",
                "message": "クエリをDiscordに転送しました（Research Cog未ロード）",
            })

        # 非同期でリサーチを実行（レスポンスは即返す）
        async def _run_research():
            try:
                await self._send_to_owner(f"🔍 **リサーチ開始**: {query}")
                result = await research_cog.run_research()
                if result:
                    await self._send_to_owner(
                        f"✅ **リサーチ完了**: {query}\n\n"
                        f"結果は定期報告で確認してください。"
                    )
            except Exception as e:
                logger.error(f"リサーチエラー: {e}")
                await self._send_to_owner(f"⚠️ リサーチエラー: {e}")

        # バックグラウンドタスクとして実行
        self.bot.loop.create_task(_run_research())

        return self._json_response({
            "status": "ok",
            "message": "リサーチを開始しました",
        })


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(ApiServer(bot))
