"""オーナー専用コマンドCog。
プロフィール管理、Bot制御などオーナーだけが使えるコマンド。
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from bot.services.owner_profile import OwnerProfile
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)


class Owner(commands.Cog):
    """オーナー専用の管理コマンド。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.profile = OwnerProfile()

    def _is_owner(self, user_id: int) -> bool:
        """オーナーかどうか判定。"""
        return user_id == OWNER_ID

    @app_commands.command(name="profile", description="オーナープロフィールを表示")
    async def show_profile(self, interaction: discord.Interaction):
        """プロフィール情報をEmbed形式で表示。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        p = self.profile._profile
        projects = p.get("current_projects", [])
        biz = p.get("business", {})
        biz_goals = biz.get("revenue_goals", {})

        embed = discord.Embed(
            title=f"⚡ {p.get('name', 'オーナー')} × Lex",
            description="Nosuke Lab パートナーシップ",
            color=discord.Color.dark_purple(),
        )
        embed.add_field(name="職業", value=p.get("role", "-"), inline=False)
        embed.add_field(name="勤務先", value=p.get("workplace", "-"), inline=False)
        embed.add_field(name="年収", value=p.get("annual_income", "-"), inline=True)
        embed.add_field(name="本業目標", value=p.get("income_goal", "-"), inline=True)
        embed.add_field(
            name="Nosuke Lab",
            value=biz.get("type", "-"),
            inline=False,
        )
        embed.add_field(
            name="事業目標",
            value=(
                f"2026: {biz_goals.get('2026', {}).get('annual_gross_profit', '-')}\n"
                f"2027: {biz_goals.get('2027', {}).get('annual_gross_profit', '-')}\n"
                f"2030: {biz_goals.get('2030', {}).get('annual_gross_profit', '-')}"
            ),
            inline=False,
        )
        embed.add_field(
            name="進行中プロジェクト",
            value=", ".join(projects) if projects else "なし",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="add_project", description="プロジェクトを追加")
    @app_commands.describe(name="プロジェクト名")
    async def add_project(self, interaction: discord.Interaction, name: str):
        """進行中プロジェクトリストにプロジェクトを追加。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        self.profile.add_project(name)
        await interaction.response.send_message(
            f"⚡ プロジェクト「{name}」を追加しました！"
        )


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(Owner(bot))
