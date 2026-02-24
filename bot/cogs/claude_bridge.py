"""Claude Bridge Cog - メッセージをClaude Code CLIにルーティング。
Lexのコア機能。ユーザーのメッセージをClaude Codeに中継し、
スマート承認システムで安全性を確保した上で結果をDiscordに返す。
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from bot.services.claude_cli import ClaudeCLIBridge
from bot.services.owner_profile import OwnerProfile
from bot.services.approval import SmartApproval, RiskLevel
from bot.services.conversation import ConversationManager
from bot.views.approval_view import ApprovalView, build_approval_embed
from bot.config import OWNER_ID, CLAUDE_TIMEOUT

logger = logging.getLogger(__name__)

# Discord メッセージの最大文字数
DISCORD_MAX_LENGTH = 1900


class ClaudeBridge(commands.Cog):
    """Claude Code CLIとDiscordを橋渡しするCog。
    スマート承認システムと統合し、リスクレベルに応じた制御を行う。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.claude = ClaudeCLIBridge(timeout=CLAUDE_TIMEOUT)
        self.profile = OwnerProfile()
        self.approval = SmartApproval()
        self.conversation = ConversationManager()

    def _is_owner(self, user_id: int) -> bool:
        """オーナーかどうか判定。"""
        return user_id == OWNER_ID

    def _split_message(self, text: str) -> list:
        """長いテキストをDiscordの文字数制限に合わせて分割。"""
        if len(text) <= DISCORD_MAX_LENGTH:
            return [text]

        chunks = []
        while text:
            if len(text) <= DISCORD_MAX_LENGTH:
                chunks.append(text)
                break

            split_pos = text.rfind("\n", 0, DISCORD_MAX_LENGTH)
            if split_pos == -1 or split_pos < DISCORD_MAX_LENGTH // 2:
                split_pos = DISCORD_MAX_LENGTH

            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")

        return chunks

    async def _process_with_approval(self, content: str, channel,
                                     reply_func, defer_func=None):
        """承認システムを通してClaude Codeを呼び出す共通処理。

        Args:
            content: ユーザーのメッセージ
            channel: Discordチャンネル
            reply_func: 応答を送信する関数
            defer_func: defer処理の関数（スラッシュコマンド用）
        """
        # リスク判定
        approval_result = self.approval.check_approval(content)
        logger.info(
            f"リスク判定: {approval_result.risk_level} "
            f"(承認: {approval_result.approved})"
        )

        # 承認が必要な場合
        if not approval_result.approved and approval_result.needs_user_input:
            # deferされている場合はfollowupで送信
            embed = build_approval_embed(approval_result, content)
            view = ApprovalView(approval_result, self.approval, content)

            if defer_func:
                # スラッシュコマンドからの呼び出し
                await reply_func(embed=embed, view=view)
            else:
                await channel.send(embed=embed, view=view)

            # ユーザーの決定を待つ
            decision = await view.wait_for_decision()

            if decision in ("deny", "timeout"):
                if decision == "timeout":
                    await channel.send("⚡ 承認がタイムアウトしました。操作をキャンセルします。")
                return  # 実行しない

        # 会話ログにユーザーメッセージを記録
        channel_name = getattr(channel, "name", "DM")
        self.conversation.add_user_message(content, channel_name)

        # Claude Code呼び出し
        # システムプロンプト = プロフィール + 会話コンテキスト
        profile_context = self.profile.get_system_context()
        conversation_context = self.conversation.get_context(max_turns=10)
        system_prompt = profile_context
        if conversation_context:
            system_prompt += "\n\n" + conversation_context

        # 全リスクレベルで10ターン・ツール制限なし
        allowed_tools = self.approval.get_allowed_tools(
            approval_result.risk_level
        )

        result = await self.claude.ask(
            content,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            max_turns=10,
        )

        # 結果を送信
        if result["success"]:
            response_text = result['text']
            chunks = self._split_message(response_text)

            # 会話ログにBot応答を記録（コスト情報含む）
            self.conversation.add_bot_response(
                content=result["text"],
                risk_level=approval_result.risk_level,
                cost_usd=result.get("cost_usd", 0),
            )

            if defer_func:
                await reply_func(chunks[0])
                for chunk in chunks[1:]:
                    await reply_func(chunk)
            else:
                for chunk in chunks:
                    await reply_func(chunk)
        else:
            error_msg = f"⚠️ エラーが発生しました: {result['error']}"
            # エラーもログに記録
            self.conversation.add_bot_response(
                content=f"[ERROR] {result['error']}",
                risk_level=approval_result.risk_level,
            )
            if defer_func:
                await reply_func(error_msg)
            else:
                await reply_func(error_msg)

    @app_commands.command(name="ask", description="Lexに質問・指示する")
    @app_commands.describe(question="質問内容")
    async def ask_command(self, interaction: discord.Interaction, question: str):
        """スラッシュコマンドでClaude Codeに質問。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        logger.info(f"/ask コマンド受信: {question[:50]}...")

        await self._process_with_approval(
            content=question,
            channel=interaction.channel,
            reply_func=interaction.followup.send,
            defer_func=True,
        )

    @app_commands.command(name="approve_list", description="承認済みリストを表示")
    async def approve_list(self, interaction: discord.Interaction):
        """ホワイトリストの内容を表示。"""
        if not self._is_owner(interaction.user.id):
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        whitelist = self.approval.get_whitelist()

        if not whitelist:
            await interaction.response.send_message(
                "⚡ 承認済みリストは空です。"
            )
            return

        embed = discord.Embed(
            title="⚡ 承認済みリスト（ホワイトリスト）",
            color=discord.Color.green(),
        )

        for i, entry in enumerate(whitelist, 1):
            embed.add_field(
                name=f"{i}. {entry.get('pattern', '不明')}",
                value=(
                    f"リスク: {entry.get('risk_level', '?')} | "
                    f"登録日: {entry.get('approved_at', '?')[:10]}\n"
                    f"メモ: {entry.get('note', 'なし')[:80]}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """メンションまたはDMに対してClaude Codeで応答。"""
        if message.author.bot:
            return

        if not self._is_owner(message.author.id):
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = (
            self.bot.user in message.mentions if message.mentions else False
        )

        if not (is_dm or is_mentioned):
            return

        content = message.content
        if self.bot.user:
            content = content.replace(f"<@{self.bot.user.id}>", "").strip()

        if not content:
            return

        logger.info(
            f"メッセージ受信（{'DM' if is_dm else 'メンション'}）: "
            f"{content[:50]}..."
        )

        async with message.channel.typing():
            await self._process_with_approval(
                content=content,
                channel=message.channel,
                reply_func=message.reply,
            )


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(ClaudeBridge(bot))
