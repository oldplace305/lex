"""一般コマンド用Cog。
/ping, /status, /cost, /restart などの基本コマンドを提供。
"""
import discord
from discord.ext import commands
from discord import app_commands
import platform
import sys
import os
import logging
from bot.services.conversation import ConversationManager
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)


class General(commands.Cog):
    """基本ユーティリティコマンド。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conversation = ConversationManager()

    @app_commands.command(name="ping", description="ボットの応答確認")
    async def ping(self, interaction: discord.Interaction):
        """Botが生きているか確認するコマンド。"""
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"⚡ pong! ({latency}ms)")

    @app_commands.command(name="status", description="ボットの状態を表示")
    async def status(self, interaction: discord.Interaction):
        """Botのステータス情報をEmbed形式で表示。"""
        embed = discord.Embed(
            title="⚡ Lex ステータス",
            color=discord.Color.blue(),
        )
        embed.add_field(name="状態", value="🟢 オンライン", inline=True)
        embed.add_field(
            name="遅延",
            value=f"{round(self.bot.latency * 1000)}ms",
            inline=True,
        )
        embed.add_field(
            name="Python",
            value=platform.python_version(),
            inline=True,
        )
        embed.add_field(
            name="discord.py",
            value=discord.__version__,
            inline=True,
        )
        embed.add_field(
            name="ホスト",
            value=platform.node(),
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="cost", description="Claude Code使用コストを表示")
    async def cost(self, interaction: discord.Interaction):
        """APIコスト統計を表示。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        stats = self.conversation.get_stats()

        embed = discord.Embed(
            title="⚡ Lex 使用状況レポート",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="💬 総メッセージ数",
            value=f"{stats['total_messages']}件",
            inline=True,
        )
        embed.add_field(
            name="🧠 メモリ保持",
            value=f"{stats['memory_turns']}ターン",
            inline=True,
        )
        embed.add_field(
            name="💰 累計APIコスト",
            value=f"${stats['total_cost_usd']:.4f}",
            inline=True,
        )
        embed.set_footer(
            text="コスト情報はClaude Code CLI (Pro $20/月 定額) のJSON出力から取得"
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="restart", description="ボットを再起動する（オーナー専用）")
    async def restart(self, interaction: discord.Interaction):
        """Botを安全に再起動する。launchd KeepAliveが自動で再起動してくれる。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "⚡ 再起動します...（数秒後に復帰します）"
        )
        logger.info("オーナーからの/restartコマンドでBot再起動を実行")

        # launchd KeepAlive が有効なので、プロセス終了後に自動で再起動される
        await self.bot.close()
        os._exit(0)

    @app_commands.command(name="help_lex", description="Lexの使い方")
    async def help_lex(self, interaction: discord.Interaction):
        """使い方ガイドを表示。"""
        embed = discord.Embed(
            title="⚡ Lex - Nosuke Lab AI Partner",
            description="しゅうたの右腕。事業推進・相談・開発なんでも。",
            color=discord.Color.dark_purple(),
        )
        embed.add_field(
            name="💬 会話する",
            value="DMを送るか、メンションしてください。",
            inline=False,
        )
        embed.add_field(
            name="📋 コマンド一覧",
            value=(
                "**基本**\n"
                "`/ping` - 応答確認\n"
                "`/status` - ステータス表示\n"
                "`/cost` - 使用状況確認\n"
                "`/restart` - 再起動\n"
                "`/help_lex` - この使い方を表示\n\n"
                "**会話・AI**\n"
                "`/ask 質問` - Lexに質問・指示\n"
                "`/approve_list` - 承認済みリスト\n\n"
                "**定期報告**\n"
                "`/report` - 今すぐレポート生成（朝/昼/夕）\n"
                "`/report_toggle` - 定期報告ON/OFF\n"
                "`/report_status` - 報告設定状況\n\n"
                "**事業管理**\n"
                "`/income` - 売上を記録\n"
                "`/expense` - 経費を記録\n"
                "`/balance` - 月次収支レポート\n"
                "`/transactions` - 取引一覧\n"
                "`/tx_delete` - 取引削除\n\n"
                "**バックアップ**\n"
                "`/backup` - 今すぐバックアップ\n"
                "`/backup_list` - バックアップ一覧\n\n"
                "**スクリプト**\n"
                "`/scripts` - スクリプト一覧\n"
                "`/run ID` - スクリプト実行\n"
                "`/script_add` - スクリプト登録\n"
                "`/script_remove` - スクリプト削除\n\n"
                "**プロフィール**\n"
                "`/profile` - プロフィール表示\n"
                "`/add_project` - プロジェクト追加"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(General(bot))
