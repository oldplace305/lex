"""スクリプト管理Cog - スクリプトの登録・実行・管理をDiscordから操作。

コマンド:
  /run <script_id>   - 登録済みスクリプトを実行
  /scripts            - 登録済みスクリプト一覧を表示
  /script_add         - 新しいスクリプトを登録
  /script_remove      - スクリプトを削除
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from bot.services.script_manager import ScriptManager
from bot.services.approval import SmartApproval, RiskLevel, ApprovalResult
from bot.views.approval_view import ApprovalView, build_approval_embed
from bot.services.claude_cli import ClaudeCLIBridge
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)


class ScriptOps(commands.Cog):
    """スクリプト管理・実行用Cog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scripts = ScriptManager()
        self.approval = SmartApproval()
        health = getattr(bot, 'health_monitor', None)
        self.claude = ClaudeCLIBridge(health_monitor=health)

    def _is_owner(self, user_id: int) -> bool:
        """オーナーかどうか判定。"""
        return user_id == OWNER_ID

    @app_commands.command(name="scripts", description="登録済みスクリプト一覧を表示")
    async def list_scripts(self, interaction: discord.Interaction):
        """スクリプト一覧を表示。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "🐟 このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        scripts = self.scripts.list_scripts()

        if not scripts:
            await interaction.response.send_message(
                "🐟 登録済みスクリプトはありません。\n"
                "`/script_add` で追加できます。"
            )
            return

        embed = discord.Embed(
            title="🐟 登録済みスクリプト",
            color=discord.Color.blue(),
        )

        for s in scripts:
            # ステータスアイコン
            status = s.get("last_status")
            if status == "success":
                status_icon = "✅"
            elif status == "failed":
                status_icon = "❌"
            else:
                status_icon = "⬜"

            # リスクレベルアイコン
            risk = s.get("risk_level", "MEDIUM")
            if risk == "HIGH":
                risk_icon = "🔴"
            elif risk == "MEDIUM":
                risk_icon = "🟡"
            else:
                risk_icon = "🟢"

            # 最終実行情報
            last_run = s.get("last_run", "未実行")
            if last_run and last_run != "未実行":
                last_run = last_run[:16].replace("T", " ")

            description_text = s.get("description", "説明なし")
            value = (
                f"{risk_icon} リスク: {risk} | {status_icon} 最終: {last_run}\n"
                f"`{s.get('command', '?')}`\n"
                f"{description_text}"
            )

            embed.add_field(
                name=f"📋 {s.get('name', s.get('id'))} (`{s.get('id')}`)",
                value=value,
                inline=False,
            )

        embed.set_footer(
            text="実行: /run <スクリプトID> | 追加: /script_add | 削除: /script_remove"
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="run", description="登録済みスクリプトを実行")
    @app_commands.describe(script_id="実行するスクリプトのID")
    async def run_script(self, interaction: discord.Interaction, script_id: str):
        """スクリプトを実行。承認システムと連携。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "🐟 このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        # スクリプトの存在確認
        script = self.scripts.get_script(script_id)
        if not script:
            available = [s.get("id") for s in self.scripts.list_scripts()]
            msg = f"🐟 スクリプト `{script_id}` が見つかりません。"
            if available:
                msg += f"\n登録済み: {', '.join(f'`{a}`' for a in available)}"
            else:
                msg += "\n`/script_add` でスクリプトを登録してください。"
            await interaction.response.send_message(msg)
            return

        # リスクレベルに基づく承認チェック
        risk_level = script.get("risk_level", "MEDIUM")
        action_pattern = f"run_script:{script_id}"

        # 承認判定
        if risk_level == "HIGH":
            # HIGH: 毎回承認必須
            approval_result = ApprovalResult(
                risk_level=RiskLevel.HIGH,
                approved=False,
                needs_user_input=True,
                reason=f"高リスクスクリプト `{script.get('name')}` の実行承認が必要です",
                action_pattern=action_pattern,
            )
        elif risk_level == "MEDIUM" and not self.approval._is_whitelisted(action_pattern):
            # MEDIUM + 未承認: 初回承認
            approval_result = ApprovalResult(
                risk_level=RiskLevel.MEDIUM,
                approved=False,
                needs_user_input=True,
                reason=f"スクリプト `{script.get('name')}` の初回実行承認が必要です",
                action_pattern=action_pattern,
            )
        else:
            # LOW or MEDIUM(承認済み): 自動実行
            approval_result = ApprovalResult(
                risk_level=risk_level,
                approved=True,
                reason="承認済み",
                action_pattern=action_pattern,
            )

        # 承認が必要な場合
        if not approval_result.approved and approval_result.needs_user_input:
            embed = build_approval_embed(
                approval_result, f"スクリプト実行: {script.get('name')}\n{script.get('command')}"
            )
            view = ApprovalView(
                approval_result, self.approval, script.get("command", "")
            )
            await interaction.response.send_message(embed=embed, view=view)

            decision = await view.wait_for_decision()

            if decision in ("deny", "timeout"):
                if decision == "timeout":
                    await interaction.followup.send(
                        "🐟 承認がタイムアウトしました。実行をキャンセルします。"
                    )
                return
        else:
            await interaction.response.defer(thinking=True)

        # スクリプト実行
        await interaction.followup.send(
            f"🐟 スクリプト `{script.get('name')}` を実行中... ⏳"
        )

        result = await self.scripts.run_script(script_id)

        # 結果の通知
        if result.success:
            embed = discord.Embed(
                title=f"✅ {script.get('name')} 実行完了",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="⏱️ 実行時間",
                value=f"{result.duration_sec}秒",
                inline=True,
            )
            embed.add_field(
                name="📤 終了コード",
                value=str(result.return_code),
                inline=True,
            )

            output = result.summary(max_length=800)
            if output:
                embed.add_field(
                    name="📋 出力",
                    value=f"```\n{output}\n```",
                    inline=False,
                )

            await interaction.followup.send(embed=embed)
        else:
            embed = discord.Embed(
                title=f"❌ {script.get('name')} 実行失敗",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="⏱️ 実行時間",
                value=f"{result.duration_sec}秒",
                inline=True,
            )
            embed.add_field(
                name="📤 終了コード",
                value=str(result.return_code),
                inline=True,
            )

            error_output = result.summary(max_length=800)
            embed.add_field(
                name="🔴 エラー内容",
                value=f"```\n{error_output}\n```",
                inline=False,
            )

            await interaction.followup.send(embed=embed)

            # エラー時の自動分析（Claude Codeに聞く）
            await self._auto_analyze_error(interaction, script, result)

    async def _auto_analyze_error(self, interaction: discord.Interaction,
                                  script: dict, result):
        """エラー時にClaude Codeで自動分析。"""
        try:
            error_info = result.summary(max_length=500)
            prompt = (
                f"以下のスクリプト実行でエラーが発生しました。原因と対処法を簡潔に教えてください。\n\n"
                f"スクリプト名: {script.get('name')}\n"
                f"コマンド: {script.get('command')}\n"
                f"終了コード: {result.return_code}\n"
                f"エラー出力:\n{error_info}\n\n"
                f"箇条書きで原因と対処法を3行以内で。"
            )

            analysis = await self.claude.ask(prompt)

            if analysis["success"] and analysis["text"]:
                embed = discord.Embed(
                    title="🐟 エラー自動分析",
                    description=analysis["text"][:1500],
                    color=discord.Color.orange(),
                )
                embed.set_footer(text="Claude Codeによる自動分析")
                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"エラー自動分析失敗: {e}")

    @app_commands.command(name="script_add", description="新しいスクリプトを登録")
    @app_commands.describe(
        script_id="スクリプトID（英数字+アンダースコア、例: monthly_report）",
        name="表示名（例: 月次レポート生成）",
        command="実行コマンド（例: python scripts/report.py）",
        description="スクリプトの説明",
        workdir="作業ディレクトリ（省略可）",
        risk_level="リスクレベル（LOW/MEDIUM/HIGH、省略時MEDIUM）",
    )
    async def add_script(self, interaction: discord.Interaction,
                         script_id: str, name: str, command: str,
                         description: str = "",
                         workdir: str = "",
                         risk_level: str = "MEDIUM"):
        """スクリプトを登録。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "🐟 このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        # リスクレベルの検証
        risk_level = risk_level.upper()
        if risk_level not in ("LOW", "MEDIUM", "HIGH"):
            await interaction.response.send_message(
                "🐟 リスクレベルは LOW / MEDIUM / HIGH のいずれかを指定してください。"
            )
            return

        success = self.scripts.add_script(
            script_id=script_id,
            name=name,
            command=command,
            workdir=workdir,
            risk_level=risk_level,
            description=description,
        )

        if success:
            embed = discord.Embed(
                title="✅ スクリプト登録完了",
                color=discord.Color.green(),
            )
            embed.add_field(name="ID", value=f"`{script_id}`", inline=True)
            embed.add_field(name="名前", value=name, inline=True)
            embed.add_field(name="リスク", value=risk_level, inline=True)
            embed.add_field(
                name="コマンド",
                value=f"```\n{command}\n```",
                inline=False,
            )
            if description:
                embed.add_field(
                    name="説明", value=description, inline=False
                )
            embed.set_footer(text=f"実行: /run {script_id}")
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"🐟 スクリプトID `{script_id}` は既に登録されています。"
            )

    @app_commands.command(name="script_remove", description="スクリプトを削除")
    @app_commands.describe(script_id="削除するスクリプトのID")
    async def remove_script(self, interaction: discord.Interaction,
                            script_id: str):
        """スクリプトを削除。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "🐟 このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        script = self.scripts.get_script(script_id)
        if not script:
            await interaction.response.send_message(
                f"🐟 スクリプト `{script_id}` が見つかりません。"
            )
            return

        success = self.scripts.remove_script(script_id)
        if success:
            await interaction.response.send_message(
                f"✅ スクリプト `{script.get('name')}` (`{script_id}`) を削除しました。"
            )
        else:
            await interaction.response.send_message(
                f"🐟 削除に失敗しました。"
            )


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(ScriptOps(bot))
