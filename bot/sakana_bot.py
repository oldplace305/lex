"""Lex Bot - メインBotクラス。
discord.py の commands.Bot を拡張し、Cog自動読み込みとステータス設定を行う。
Lex = Nosuke Labの一員。しゅうたの右腕として事業を推進する。
"""
import discord
from discord.ext import commands
import logging
from bot.config import OWNER_ID, BOT_PREFIX

logger = logging.getLogger(__name__)


class LexBot(commands.Bot):
    """Lex Botのメインクラス。"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # メッセージ内容の読み取り
        intents.members = True          # メンバー情報の取得

        super().__init__(
            command_prefix=BOT_PREFIX,
            intents=intents,
            owner_id=OWNER_ID,
        )

    async def setup_hook(self):
        """Bot起動時にCogを読み込み、スラッシュコマンドを同期。"""
        cog_list = [
            "bot.cogs.general",
            "bot.cogs.claude_bridge",
            "bot.cogs.owner",
            "bot.cogs.script_ops",
            "bot.cogs.daily_report",
            "bot.cogs.business",
            "bot.cogs.backup",
        ]

        for cog in cog_list:
            try:
                await self.load_extension(cog)
                logger.info(f"Cog読み込み成功: {cog}")
            except Exception as e:
                logger.error(f"Cog読み込み失敗 {cog}: {e}", exc_info=True)

        # スラッシュコマンドをDiscordに同期
        try:
            synced = await self.tree.sync()
            logger.info(f"スラッシュコマンド同期完了: {len(synced)}個")
        except Exception as e:
            logger.error(f"スラッシュコマンド同期失敗: {e}", exc_info=True)

    async def on_ready(self):
        """Bot起動完了時のイベント。"""
        logger.info(f"⚡ Lex オンライン: {self.user} (ID: {self.user.id})")
        logger.info(f"接続サーバー数: {len(self.guilds)}")

        # ステータスメッセージを設定
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Nosuke Lab を推進中 ⚡",
            )
        )

    async def on_command_error(self, ctx, error):
        """コマンドエラーのハンドリング。"""
        if isinstance(error, commands.CommandNotFound):
            return  # 不明なコマンドは無視
        logger.error(f"コマンドエラー: {error}", exc_info=True)
