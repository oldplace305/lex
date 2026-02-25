"""定期報告Cog - 1日3回の自動レポートをDiscordに送信。
Lexが1日3回（朝9:00、昼12:00、夕18:00）、事業状況を報告する。
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timezone, timedelta, time
from bot.services.claude_cli import ClaudeCLIBridge
from bot.services.owner_profile import OwnerProfile
from bot.services.conversation import ConversationManager
from bot.config import OWNER_ID, REPORT_CHANNEL_ID

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# レポート時刻（JST）
AM_REPORT_HOUR = 9    # 午前9時
NOON_REPORT_HOUR = 12 # 昼12時
PM_REPORT_HOUR = 18   # 午後6時

# AMレポートのプロンプト
AM_REPORT_PROMPT = """
あなたはLex。Nosuke Labの朝の定期報告を行ってください。

以下の構成で簡潔にまとめてください（合計300文字以内目標）：

⚡ おはようございます、しゅうた。

1. 📅 今日の日付・曜日
2. 🎯 今日やるべきこと（Nosuke Labの優先タスク）
3. 💡 提案（事業推進のためにできること1つ）
4. ⚡ ひとこと（モチベーション）

※ 自然な日本語で。JSONやメタデータは不要。
""".strip()

# 昼レポートのプロンプト
NOON_REPORT_PROMPT = """
あなたはLex。Nosuke Labの昼の定期報告を行ってください。

以下の構成で簡潔にまとめてください（合計300文字以内目標）：

⚡ しゅうた、お昼の報告です。

1. 📈 午前の進捗（Nosuke Lab関連で動いたこと）
2. 🔍 市場・業界の気になる動き（あれば1つ）
3. 🎯 午後にやるべきこと（優先順位付き）
4. ⚡ ひとこと

※ 自然な日本語で。JSONやメタデータは不要。
""".strip()

# PMレポートのプロンプト
PM_REPORT_PROMPT = """
あなたはLex。Nosuke Labの夕方の定期報告を行ってください。

以下の構成で簡潔にまとめてください（合計300文字以内目標）：

⚡ おつかれさまです、しゅうた。

1. 📊 本日の振り返り（Nosuke Lab関連で進んだこと・気づき）
2. 📋 明日に向けて（優先すべきこと）
3. 💰 コスト状況（今月の注意点があれば）
4. ⚡ ひとこと（おつかれさまメッセージ）

※ 自然な日本語で。JSONやメタデータは不要。
""".strip()


class DailyReport(commands.Cog):
    """定期報告を自動送信するCog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        health = getattr(bot, 'health_monitor', None)
        self.claude = ClaudeCLIBridge(health_monitor=health)
        self.profile = OwnerProfile()
        self.conversation = ConversationManager()
        self._report_enabled = True

    async def cog_load(self):
        """Cog読み込み時にスケジューラーを開始。"""
        self.daily_report_loop.start()
        logger.info("⚡ 定期報告スケジューラー開始")

    async def cog_unload(self):
        """Cog解除時にスケジューラーを停止。"""
        self.daily_report_loop.cancel()
        logger.info("⚡ 定期報告スケジューラー停止")

    def _get_report_channel(self) -> discord.TextChannel:
        """レポート送信先チャンネルを取得。"""
        if REPORT_CHANNEL_ID:
            channel = self.bot.get_channel(REPORT_CHANNEL_ID)
            if channel:
                return channel
            logger.warning(
                f"REPORT_CHANNEL_ID ({REPORT_CHANNEL_ID}) が見つかりません。"
                "DMにフォールバックします。"
            )
        return None

    async def _send_to_owner(self, content: str):
        """オーナーにメッセージを送信（チャンネル or DM）。"""
        # チャンネル指定がある場合はそこに送信
        channel = self._get_report_channel()
        if channel:
            await channel.send(content)
            return

        # フォールバック: オーナーにDM
        try:
            owner = await self.bot.fetch_user(OWNER_ID)
            if owner:
                await owner.send(content)
        except Exception as e:
            logger.error(f"オーナーへのDM送信失敗: {e}")

    async def _generate_report(self, report_type: str) -> str:
        """Claude CLIを使ってレポートを生成。

        Args:
            report_type: "am", "noon", or "pm"

        Returns:
            str: レポートテキスト
        """
        prompts = {"am": AM_REPORT_PROMPT, "noon": NOON_REPORT_PROMPT, "pm": PM_REPORT_PROMPT}
        prompt = prompts.get(report_type, AM_REPORT_PROMPT)
        system_prompt = self.profile.get_system_context()

        # 会話コンテキストも含める（直近の活動を把握するため）
        conversation_context = self.conversation.get_context(max_turns=5)
        if conversation_context:
            system_prompt += "\n\n" + conversation_context

        result = await self.claude.ask(
            prompt,
            system_prompt=system_prompt,
            max_turns=3,
        )

        if result["success"]:
            # コスト記録
            self.conversation.add_bot_response(
                content=result["text"],
                risk_level="LOW",
                cost_usd=result.get("cost_usd", 0),
            )
            return result["text"]
        else:
            logger.error(f"定期報告生成エラー: {result['error']}")
            return f"⚠️ 定期報告の生成に失敗しました: {result['error']}"

    @tasks.loop(minutes=1)
    async def daily_report_loop(self):
        """毎分チェックし、レポート時刻になったら報告を送信。"""
        if not self._report_enabled:
            return

        now = datetime.now(JST)
        current_hour = now.hour
        current_minute = now.minute

        # 毎時0分にのみチェック（1分以内の精度）
        if current_minute != 0:
            return

        if current_hour == AM_REPORT_HOUR:
            logger.info("⚡ 朝定期報告を生成開始")
            report = await self._generate_report("am")
            await self._send_to_owner(report)
            logger.info("⚡ 朝定期報告送信完了")

        elif current_hour == NOON_REPORT_HOUR:
            logger.info("⚡ 昼定期報告を生成開始")
            report = await self._generate_report("noon")
            await self._send_to_owner(report)
            logger.info("⚡ 昼定期報告送信完了")

        elif current_hour == PM_REPORT_HOUR:
            logger.info("⚡ 夕定期報告を生成開始")
            report = await self._generate_report("pm")
            await self._send_to_owner(report)
            logger.info("⚡ 夕定期報告送信完了")

    @daily_report_loop.before_loop
    async def before_daily_report(self):
        """Bot起動完了を待つ。"""
        await self.bot.wait_until_ready()
        logger.info("⚡ 定期報告: Bot準備完了。スケジュール監視開始。")

    # --- スラッシュコマンド ---

    @app_commands.command(
        name="report", description="定期報告を今すぐ生成する（午前/午後）"
    )
    @app_commands.describe(
        report_type="レポートの種類（am=朝, noon=昼, pm=夕）"
    )
    @app_commands.choices(
        report_type=[
            app_commands.Choice(name="🌅 朝報告", value="am"),
            app_commands.Choice(name="☀️ 昼報告", value="noon"),
            app_commands.Choice(name="🌆 夕報告", value="pm"),
        ]
    )
    async def report_now(
        self, interaction: discord.Interaction, report_type: str = "am"
    ):
        """手動で定期報告を生成。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        logger.info(f"/report コマンド受信: {report_type}")

        report = await self._generate_report(report_type)
        await interaction.followup.send(report)

    @app_commands.command(
        name="report_toggle", description="定期報告のON/OFF切り替え"
    )
    async def report_toggle(self, interaction: discord.Interaction):
        """定期報告の有効/無効を切り替え。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        self._report_enabled = not self._report_enabled
        status = "✅ 有効" if self._report_enabled else "❌ 無効"
        await interaction.response.send_message(
            f"⚡ 定期報告: {status}\n"
            f"朝 {AM_REPORT_HOUR}:00 / 昼 {NOON_REPORT_HOUR}:00 / 夕 {PM_REPORT_HOUR}:00（JST）"
        )
        logger.info(f"定期報告切り替え: {status}")

    @app_commands.command(
        name="report_status", description="定期報告の設定状況を表示"
    )
    async def report_status(self, interaction: discord.Interaction):
        """定期報告の現在の設定を表示。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        channel = self._get_report_channel()
        channel_info = f"#{channel.name}" if channel else "DM（オーナー宛）"

        embed = discord.Embed(
            title="⚡ 定期報告 設定状況",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="ステータス",
            value="✅ 有効" if self._report_enabled else "❌ 無効",
            inline=True,
        )
        embed.add_field(
            name="朝報告",
            value=f"{AM_REPORT_HOUR}:00 JST",
            inline=True,
        )
        embed.add_field(
            name="昼報告",
            value=f"{NOON_REPORT_HOUR}:00 JST",
            inline=True,
        )
        embed.add_field(
            name="夕報告",
            value=f"{PM_REPORT_HOUR}:00 JST",
            inline=True,
        )
        embed.add_field(
            name="送信先",
            value=channel_info,
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(DailyReport(bot))
