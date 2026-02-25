"""ヘルスモニタリング Cog。
/health コマンドと、自動ヘルスチェック + オーナーDM通知を提供。

Phase 2: 基本モニタリング
Phase 3: 自己修復連携（auto_diagnose）
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)


class Health(commands.Cog):
    """Lex のヘルスモニタリングと自動通知。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_notified = False  # 重複通知防止

    async def cog_load(self):
        """Cog読み込み時にヘルスチェックループを開始。"""
        self.health_check_loop.start()

    async def cog_unload(self):
        """Cog解放時にループを停止。"""
        self.health_check_loop.cancel()

    @app_commands.command(name="health", description="Lexの健康状態を表示")
    async def health_check(self, interaction: discord.Interaction):
        """ヘルスレポートをEmbed形式で表示。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        health = getattr(self.bot, 'health_monitor', None)
        if not health:
            await interaction.response.send_message(
                "⚠️ ヘルスモニターが利用できません。"
            )
            return

        report = health.get_health_report()

        embed = discord.Embed(
            title=f"⚡ Lex ヘルスレポート",
            description=f"ステータス: {report['status']}",
            color=(discord.Color.green()
                   if report['status_healthy']
                   else discord.Color.red()),
        )

        if report['attention_reason']:
            embed.add_field(
                name="⚠️ 注意事項",
                value=report['attention_reason'],
                inline=False,
            )

        embed.add_field(name="稼働時間", value=report['uptime'], inline=True)
        embed.add_field(name="起動時刻", value=report['boot_time'], inline=True)
        embed.add_field(name="", value="", inline=True)  # spacer

        embed.add_field(
            name="CLI呼び出し",
            value=f"{report['total_cli_calls']}回",
            inline=True,
        )
        embed.add_field(
            name="成功率",
            value=report['success_rate'],
            inline=True,
        )
        embed.add_field(
            name="累計コスト",
            value=f"${report['total_cost_usd']:.4f}",
            inline=True,
        )

        embed.add_field(
            name="エラー/タイムアウト/max-turns",
            value=(
                f"{report['total_cli_errors']} / "
                f"{report['total_timeouts']} / "
                f"{report['total_max_turns']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="連続失敗",
            value=f"{report['consecutive_failures']}回",
            inline=True,
        )
        embed.add_field(
            name="最後の成功",
            value=report['last_successful_call'],
            inline=True,
        )

        if report['last_error']:
            embed.add_field(
                name="最後のエラー",
                value=f"{report['last_error_time']}: {report['last_error'][:100]}",
                inline=False,
            )

        embed.set_footer(text="5分ごとに自動チェック実行中")
        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=5)
    async def health_check_loop(self):
        """5分ごとにヘルスチェックを実行し、問題があればオーナーにDM通知。"""
        health = getattr(self.bot, 'health_monitor', None)
        if not health:
            return

        needs_attention, reason = health.needs_attention()

        if needs_attention and not self._last_notified:
            # オーナーにDM通知
            await self._notify_owner(
                f"⚠️ **Lex ヘルスアラート**\n\n"
                f"問題: {reason}\n\n"
                f"`/health` で詳細を確認してください。\n"
                f"`/diagnose` で自己診断を実行できます。"
            )
            self._last_notified = True
            logger.warning(f"ヘルスアラート送信: {reason}")

            # Phase 3: 自動診断トリガー
            repair_cog = self.bot.get_cog("SelfRepair")
            if repair_cog:
                try:
                    await repair_cog.auto_diagnose(reason)
                except Exception as e:
                    logger.error(f"自動診断トリガーエラー: {e}")

        elif not needs_attention:
            if self._last_notified:
                # 回復通知
                await self._notify_owner("✅ Lex は正常な状態に回復しました。")
                logger.info("ヘルス正常回復通知送信")
            self._last_notified = False

    @health_check_loop.before_loop
    async def before_health_check(self):
        """Bot起動完了を待つ。"""
        await self.bot.wait_until_ready()

    async def _notify_owner(self, message: str):
        """オーナーにDMで通知を送信。"""
        try:
            owner = await self.bot.fetch_user(OWNER_ID)
            if owner:
                await owner.send(message)
        except Exception as e:
            logger.error(f"オーナー通知失敗: {e}")


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(Health(bot))
