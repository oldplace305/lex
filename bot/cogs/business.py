"""事業管理Cog - Nosuke Labの売上・経費・収支管理。
/income, /expense, /balance, /transactions コマンドを提供。
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from bot.services.business_manager import BusinessManager
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)

# 経費カテゴリ選択肢
EXPENSE_CATEGORIES = [
    app_commands.Choice(name="🖥️ サーバー/インフラ", value="サーバー/インフラ"),
    app_commands.Choice(name="🤖 AI/API費用", value="AI/API費用"),
    app_commands.Choice(name="🔧 ツール/サブスク", value="ツール/サブスク"),
    app_commands.Choice(name="📚 書籍/教材", value="書籍/教材"),
]

# 売上カテゴリ選択肢
INCOME_CATEGORIES = [
    app_commands.Choice(name="📝 note販売", value="note販売"),
    app_commands.Choice(name="💼 コンサル", value="コンサル"),
    app_commands.Choice(name="🎨 デザイン/制作", value="デザイン/制作"),
    app_commands.Choice(name="📱 SNS代行", value="SNS代行"),
]


class Business(commands.Cog):
    """Nosuke Lab事業管理コマンド。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.biz = BusinessManager()

    def _is_owner(self, user_id: int) -> bool:
        return user_id == OWNER_ID

    # --- 売上登録 ---
    @app_commands.command(name="income", description="売上を記録する")
    @app_commands.describe(
        amount="金額（円）",
        category="カテゴリ",
        memo="メモ（任意）",
    )
    @app_commands.choices(category=INCOME_CATEGORIES)
    async def add_income(
        self, interaction: discord.Interaction,
        amount: int, category: str, memo: str = "",
    ):
        """売上を登録。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        tx = self.biz.add_transaction("income", amount, category, memo)
        monthly = self.biz.get_monthly_summary()

        embed = discord.Embed(
            title="⚡ 売上登録完了",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="登録内容",
            value=f"💰 ¥{amount:,}  [{category}]\n{memo}" if memo else f"💰 ¥{amount:,}  [{category}]",
            inline=False,
        )
        embed.add_field(
            name=f"📊 {monthly['year_month']} 月次",
            value=(
                f"売上: ¥{monthly['income']:,}\n"
                f"経費: ¥{monthly['expense']:,}\n"
                f"利益: ¥{monthly['profit']:,}"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    # --- 経費登録 ---
    @app_commands.command(name="expense", description="経費を記録する")
    @app_commands.describe(
        amount="金額（円）",
        category="カテゴリ",
        memo="メモ（任意）",
    )
    @app_commands.choices(category=EXPENSE_CATEGORIES)
    async def add_expense(
        self, interaction: discord.Interaction,
        amount: int, category: str, memo: str = "",
    ):
        """経費を登録。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        tx = self.biz.add_transaction("expense", amount, category, memo)
        monthly = self.biz.get_monthly_summary()

        embed = discord.Embed(
            title="⚡ 経費登録完了",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="登録内容",
            value=f"💸 ¥{amount:,}  [{category}]\n{memo}" if memo else f"💸 ¥{amount:,}  [{category}]",
            inline=False,
        )
        embed.add_field(
            name=f"📊 {monthly['year_month']} 月次",
            value=(
                f"売上: ¥{monthly['income']:,}\n"
                f"経費: ¥{monthly['expense']:,}\n"
                f"利益: ¥{monthly['profit']:,}"
            ),
            inline=False,
        )
        # 予算警告
        if monthly["budget_remaining"] is not None and monthly["budget_remaining"] < 0:
            embed.add_field(
                name="⚠️ 予算超過",
                value=f"月間予算 ¥{monthly['monthly_budget']:,} を ¥{abs(monthly['budget_remaining']):,} 超過中",
                inline=False,
            )
        elif monthly["budget_remaining"] is not None:
            embed.add_field(
                name="💰 予算残",
                value=f"¥{monthly['budget_remaining']:,} / ¥{monthly['monthly_budget']:,}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # --- 収支確認 ---
    @app_commands.command(name="balance", description="今月の収支サマリーを表示")
    @app_commands.describe(month="対象月（YYYY-MM形式、省略で当月）")
    async def balance(
        self, interaction: discord.Interaction, month: str = None,
    ):
        """月次収支を表示。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        monthly = self.biz.get_monthly_summary(month)
        yearly = self.biz.get_yearly_summary()
        breakdown = self.biz.get_category_breakdown(month)

        embed = discord.Embed(
            title=f"⚡ Nosuke Lab 収支レポート [{monthly['year_month']}]",
            color=discord.Color.gold(),
        )

        # 月次
        profit_emoji = "📈" if monthly["profit"] >= 0 else "📉"
        embed.add_field(
            name=f"{profit_emoji} 月次サマリー",
            value=(
                f"売上: ¥{monthly['income']:,}\n"
                f"経費: ¥{monthly['expense']:,}\n"
                f"**利益: ¥{monthly['profit']:,}**"
            ),
            inline=True,
        )

        # 予算
        if monthly["monthly_budget"]:
            budget_pct = round(monthly["expense"] / monthly["monthly_budget"] * 100) if monthly["monthly_budget"] else 0
            embed.add_field(
                name="💰 月間予算",
                value=(
                    f"使用: ¥{monthly['expense']:,} / ¥{monthly['monthly_budget']:,}\n"
                    f"消化率: {budget_pct}%\n"
                    f"残: ¥{monthly['budget_remaining']:,}"
                ),
                inline=True,
            )

        # 年次目標
        if yearly["goal"]:
            embed.add_field(
                name=f"🎯 {yearly['year']}年目標",
                value=(
                    f"年間粗利: ¥{yearly['profit']:,} / ¥{yearly['goal']:,}\n"
                    f"達成率: {yearly['progress_pct']}%"
                ),
                inline=False,
            )

        # カテゴリ別経費
        if breakdown["expense_by_category"]:
            lines = []
            for cat, amt in sorted(
                breakdown["expense_by_category"].items(),
                key=lambda x: x[1], reverse=True,
            ):
                lines.append(f"  {cat}: ¥{amt:,}")
            embed.add_field(
                name="📋 経費内訳",
                value="\n".join(lines),
                inline=False,
            )

        # カテゴリ別売上
        if breakdown["income_by_category"]:
            lines = []
            for cat, amt in sorted(
                breakdown["income_by_category"].items(),
                key=lambda x: x[1], reverse=True,
            ):
                lines.append(f"  {cat}: ¥{amt:,}")
            embed.add_field(
                name="📋 売上内訳",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text=f"取引数: {monthly['transaction_count']}件")
        await interaction.response.send_message(embed=embed)

    # --- 取引一覧 ---
    @app_commands.command(name="transactions", description="直近の取引一覧を表示")
    @app_commands.describe(limit="表示件数（デフォルト10）")
    async def transactions(
        self, interaction: discord.Interaction, limit: int = 10,
    ):
        """直近の取引を表示。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        txs = self.biz.get_recent_transactions(limit)

        if not txs:
            await interaction.response.send_message(
                "⚡ まだ取引が登録されていません。\n"
                "`/income` で売上、`/expense` で経費を記録できます。"
            )
            return

        embed = discord.Embed(
            title=f"⚡ 直近の取引（{len(txs)}件）",
            color=discord.Color.blue(),
        )

        for tx in txs:
            emoji = "💰" if tx["type"] == "income" else "💸"
            sign = "+" if tx["type"] == "income" else "-"
            memo = f" | {tx['description']}" if tx.get("description") else ""
            embed.add_field(
                name=f"{emoji} {tx['date']} [{tx['category']}]",
                value=f"{sign}¥{tx['amount']:,}{memo}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # --- 取引削除 ---
    @app_commands.command(name="tx_delete", description="取引を削除する")
    @app_commands.describe(tx_id="削除する取引ID（/transactionsで確認）")
    async def tx_delete(
        self, interaction: discord.Interaction, tx_id: int,
    ):
        """取引を削除。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        if self.biz.delete_transaction(tx_id):
            await interaction.response.send_message(
                f"⚡ 取引 ID:{tx_id} を削除しました。"
            )
        else:
            await interaction.response.send_message(
                f"⚠️ 取引 ID:{tx_id} が見つかりません。", ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(Business(bot))
