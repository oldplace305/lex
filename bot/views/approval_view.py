"""Discord UIボタンによる承認フロー。
ユーザーにリスクレベルと操作内容を提示し、
ボタンで承認/拒否を選択してもらう。
"""
import asyncio
import discord
from discord.ui import View, Button
import logging
from bot.services.approval import SmartApproval, ApprovalResult, RiskLevel
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)

# 承認タイムアウト（秒）
APPROVAL_TIMEOUT = 300  # 5分
REMINDER_AFTER = 30     # 30秒後にリマインド


class ApprovalView(View):
    """承認ボタンを表示するDiscord UIビュー。

    ボタン:
    - ✅ 許可（今回のみ）
    - ✅ 許可（今後も自動実行OK）← ホワイトリスト追加
    - ❌ 拒否
    """

    def __init__(self, approval_result: ApprovalResult,
                 smart_approval: SmartApproval,
                 original_message: str):
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.approval_result = approval_result
        self.smart_approval = smart_approval
        self.original_message = original_message
        self.user_decision = None  # "approve_once", "approve_always", "deny"
        self._event = asyncio.Event()
        self._reminded = False

    @discord.ui.button(
        label="許可（今回のみ）",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="approve_once",
    )
    async def approve_once(self, interaction: discord.Interaction,
                           button: Button):
        """今回のみ許可。"""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⚡ オーナーのみが承認できます。", ephemeral=True
            )
            return

        self.user_decision = "approve_once"
        self._event.set()
        await interaction.response.edit_message(
            content="✅ **承認しました**（今回のみ）",
            view=None,
        )
        logger.info(f"承認（今回のみ）: {self.approval_result.action_pattern}")

    @discord.ui.button(
        label="許可（今後も自動OK）",
        style=discord.ButtonStyle.primary,
        emoji="🔓",
        custom_id="approve_always",
    )
    async def approve_always(self, interaction: discord.Interaction,
                             button: Button):
        """今後も自動実行OK（ホワイトリスト追加）。"""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⚡ オーナーのみが承認できます。", ephemeral=True
            )
            return

        # HIGH リスクはホワイトリスト追加不可
        if self.approval_result.risk_level == RiskLevel.HIGH:
            self.user_decision = "approve_once"
            self._event.set()
            await interaction.response.edit_message(
                content="✅ **承認しました**（高リスク操作のため今回のみ有効）",
                view=None,
            )
        else:
            # ホワイトリストに追加
            self.smart_approval.add_to_whitelist(
                action_pattern=self.approval_result.action_pattern,
                risk_level=self.approval_result.risk_level,
                note=self.original_message[:100],
            )
            self.user_decision = "approve_always"
            self._event.set()
            await interaction.response.edit_message(
                content=(
                    "✅ **承認しました**（今後は自動実行されます）\n"
                    f"📋 登録パターン: `{self.approval_result.action_pattern}`"
                ),
                view=None,
            )

        logger.info(
            f"承認（自動登録）: {self.approval_result.action_pattern}"
        )

    @discord.ui.button(
        label="拒否",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="deny",
    )
    async def deny(self, interaction: discord.Interaction, button: Button):
        """拒否。"""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⚡ オーナーのみが操作できます。", ephemeral=True
            )
            return

        self.user_decision = "deny"
        self._event.set()
        await interaction.response.edit_message(
            content="❌ **拒否しました**。操作はキャンセルされました。",
            view=None,
        )
        logger.info(f"拒否: {self.approval_result.action_pattern}")

    async def on_timeout(self):
        """タイムアウト時の処理。"""
        self.user_decision = "timeout"
        self._event.set()
        logger.info(
            f"承認タイムアウト: {self.approval_result.action_pattern}"
        )

    async def wait_for_decision(self) -> str:
        """ユーザーの決定を待つ。

        Returns:
            "approve_once", "approve_always", "deny", "timeout"
        """
        await self._event.wait()
        return self.user_decision


def build_approval_embed(approval_result: ApprovalResult,
                         original_message: str) -> discord.Embed:
    """承認リクエストのEmbedを生成する。

    Args:
        approval_result: リスク判定結果
        original_message: 元のユーザーメッセージ

    Returns:
        discord.Embed: 承認リクエストEmbed
    """
    # リスクレベルに応じた色とアイコン
    level = approval_result.risk_level
    if level == RiskLevel.HIGH:
        color = discord.Color.red()
        level_icon = "🔴"
        level_text = "HIGH（危険な操作）"
    elif level == RiskLevel.MEDIUM:
        color = discord.Color.yellow()
        level_icon = "🟡"
        level_text = "MEDIUM（ファイル操作を含む）"
    else:
        color = discord.Color.green()
        level_icon = "🟢"
        level_text = "LOW"

    embed = discord.Embed(
        title="⚡ 操作の実行許可が必要です",
        description=approval_result.reason,
        color=color,
    )

    # 操作内容（長すぎる場合は切り詰め）
    content_preview = original_message[:200]
    if len(original_message) > 200:
        content_preview += "..."

    embed.add_field(
        name="📋 操作内容",
        value=f"```\n{content_preview}\n```",
        inline=False,
    )
    embed.add_field(
        name=f"{level_icon} リスクレベル",
        value=level_text,
        inline=True,
    )
    embed.add_field(
        name="🏷️ パターン",
        value=f"`{approval_result.action_pattern}`",
        inline=True,
    )

    if level == RiskLevel.HIGH:
        embed.set_footer(
            text="⚠️ 高リスク操作はホワイトリスト登録できません。毎回承認が必要です。"
        )
    else:
        embed.set_footer(
            text="💡「今後も自動OK」を選ぶと、同じ操作は次回から自動実行されます。"
        )

    return embed
