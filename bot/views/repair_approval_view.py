"""修復承認 View。
自己修復の提案をオーナーに表示し、承認/拒否を受け付ける。

Phase 4: 自律修復の承認UI
"""
import asyncio
import discord
from discord.ui import View, Button
import logging

logger = logging.getLogger(__name__)

# 承認タイムアウト（秒）
REPAIR_APPROVAL_TIMEOUT = 300  # 5分


class RepairApprovalView(View):
    """修復提案の承認UI。"""

    def __init__(self, diagnosis: dict, owner_id: int):
        super().__init__(timeout=REPAIR_APPROVAL_TIMEOUT)
        self.diagnosis = diagnosis
        self.owner_id = owner_id
        self._decision = None
        self._event = asyncio.Event()

    @discord.ui.button(
        label="🔍 診断のみ",
        style=discord.ButtonStyle.secondary,
        custom_id="repair_diagnose_only",
    )
    async def diagnose_only(self, interaction: discord.Interaction,
                            button: Button):
        """診断結果の確認のみ。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚡ オーナーのみが操作できます。", ephemeral=True
            )
            return

        self._decision = "diagnose_only"
        self._event.set()
        await interaction.response.edit_message(
            content="🔍 診断のみ実行します。",
            view=None,
        )

    @discord.ui.button(
        label="🔧 修復実行",
        style=discord.ButtonStyle.danger,
        custom_id="repair_execute",
    )
    async def execute_repair(self, interaction: discord.Interaction,
                             button: Button):
        """修復を実行。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚡ オーナーのみが操作できます。", ephemeral=True
            )
            return

        self._decision = "execute"
        self._event.set()
        await interaction.response.edit_message(
            content="🔧 修復を実行中...",
            view=None,
        )

    @discord.ui.button(
        label="❌ キャンセル",
        style=discord.ButtonStyle.secondary,
        custom_id="repair_cancel",
    )
    async def cancel_repair(self, interaction: discord.Interaction,
                            button: Button):
        """修復をキャンセル。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚡ オーナーのみが操作できます。", ephemeral=True
            )
            return

        self._decision = "cancel"
        self._event.set()
        await interaction.response.edit_message(
            content="❌ 修復をキャンセルしました。",
            view=None,
        )

    async def wait_for_decision(self) -> str:
        """ユーザーの決定を待つ。

        Returns:
            "execute" / "diagnose_only" / "cancel" / "timeout"
        """
        try:
            await asyncio.wait_for(
                self._event.wait(),
                timeout=REPAIR_APPROVAL_TIMEOUT,
            )
            return self._decision
        except asyncio.TimeoutError:
            return "timeout"


def build_repair_embed(diagnosis: dict) -> discord.Embed:
    """診断結果からEmbed を構築。"""
    severity = diagnosis.get("severity", "unknown")
    severity_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(
        severity, "⚪"
    )

    embed = discord.Embed(
        title="🔧 Lex 自己修復提案",
        description=diagnosis.get("summary", diagnosis.get("diagnosis", "")),
        color={
            "low": discord.Color.green(),
            "medium": discord.Color.gold(),
            "high": discord.Color.red(),
        }.get(severity, discord.Color.greyple()),
    )

    embed.add_field(
        name="深刻度",
        value=f"{severity_emoji} {severity}",
        inline=True,
    )
    embed.add_field(
        name="自動修復",
        value="可能" if diagnosis.get("can_auto_fix") else "不可",
        inline=True,
    )
    embed.add_field(
        name="再起動",
        value="必要" if diagnosis.get("needs_restart") else "不要",
        inline=True,
    )

    fixes = diagnosis.get("proposed_fixes", [])
    if fixes:
        fix_text = "\n".join(
            f"• `{f['file']}`: {f['description']}" for f in fixes[:5]
        )
        embed.add_field(
            name="提案修正",
            value=fix_text[:1024],
            inline=False,
        )

    embed.set_footer(text="修復実行するとGitブランチが作成されます（ロールバック可能）")
    return embed
