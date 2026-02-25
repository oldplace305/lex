"""自己修復 Cog。
/diagnose, /repair コマンドと、自然言語での修復トリガーを提供。

Phase 3: Discord UI + 自動診断連携
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from bot.services.self_repair import SelfRepairService
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)

# 自己修復を検知するキーワード
SELF_REPAIR_KEYWORDS = [
    "エラーを解消", "エラーを直して", "エラー修正",
    "バグ直して", "バグ修正して", "自分を修正",
    "自己修復", "fix yourself", "heal yourself",
    "自分のコードを直", "診断して",
]


class SelfRepair(commands.Cog):
    """Lex の自己診断・修復コマンド。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repair_service = SelfRepairService(bot)

    @app_commands.command(
        name="diagnose",
        description="Lexが自分自身のエラーを診断する（読み取り専用）"
    )
    async def diagnose(self, interaction: discord.Interaction):
        """自己診断を実行（コード変更なし）。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        logger.info("/diagnose コマンド受信")

        result = await self.repair_service.diagnose(trigger="manual")

        # 2000文字制限対応
        message = result["message"]
        if len(message) > 1900:
            message = message[:1900] + "\n..."

        await interaction.followup.send(message)

    @app_commands.command(
        name="repair",
        description="Lexが自分自身のエラーを診断し修復する"
    )
    async def repair(self, interaction: discord.Interaction):
        """自己修復を実行（診断 → 承認 → 修復）。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        logger.info("/repair コマンド受信")

        result = await self.repair_service.attempt_repair(trigger="user_request")

        message = result["message"]
        if len(message) > 1900:
            message = message[:1900] + "\n..."

        await interaction.followup.send(message)

    async def auto_diagnose(self, reason: str):
        """ヘルスモニターからの自動診断（修復は実行しない）。

        Args:
            reason: ヘルスアラートの理由
        """
        logger.info(f"自動診断トリガー: {reason}")
        result = await self.repair_service.diagnose(trigger="auto_health")

        # オーナーにDMで診断結果を送信
        if result["attempted"]:
            try:
                owner = await self.bot.fetch_user(OWNER_ID)
                if owner:
                    message = (
                        f"🔧 **自動診断結果**\n"
                        f"トリガー: {reason}\n\n"
                        f"{result['message'][:1500]}\n\n"
                        f"修復するには `/repair` を実行してください。"
                    )
                    await owner.send(message)
            except Exception as e:
                logger.error(f"自動診断結果の通知失敗: {e}")

    def is_repair_request(self, content: str) -> bool:
        """メッセージが自己修復リクエストかどうか判定。"""
        content_lower = content.lower()
        return any(kw in content_lower for kw in SELF_REPAIR_KEYWORDS)


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(SelfRepair(bot))
