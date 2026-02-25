"""Claude Bridge Cog - メッセージをClaude Code CLIにルーティング。
Lexのコア機能。ユーザーのメッセージをClaude Codeに中継し、
スマート承認システムで安全性を確保した上で結果をDiscordに返す。

Phase 1改善:
- _safe_reply: Interaction期限切れ時のフォールバック
- _progress_notifier: 長時間処理の進捗通知
- max_turns=10固定を廃止、プロファイル自動判定に委ねる
"""
import asyncio
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
        # ヘルスモニターが利用可能ならCLIに渡す
        health = getattr(bot, 'health_monitor', None)
        self.claude = ClaudeCLIBridge(health_monitor=health)
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

    async def _safe_reply(self, reply_func, channel,
                          content=None, embed=None, view=None):
        """応答を送信。Interaction期限切れ時はchannel.sendにフォールバック。

        Args:
            reply_func: 通常の応答関数（message.reply or interaction.followup.send）
            channel: フォールバック先のチャンネル
            content: テキストメッセージ
            embed: Embedオブジェクト
            view: Viewオブジェクト
        """
        try:
            kwargs = {}
            if content is not None:
                kwargs["content"] = content
            if embed is not None:
                kwargs["embed"] = embed
            if view is not None:
                kwargs["view"] = view
            await reply_func(**kwargs)
        except (discord.errors.NotFound, discord.errors.HTTPException) as e:
            logger.warning(
                f"応答送信失敗（Interaction期限切れ等）、"
                f"channel.sendにフォールバック: {e}"
            )
            try:
                kwargs_fallback = {}
                if content is not None:
                    kwargs_fallback["content"] = content
                if embed is not None:
                    kwargs_fallback["embed"] = embed
                if view is not None:
                    kwargs_fallback["view"] = view
                await channel.send(**kwargs_fallback)
            except Exception as e2:
                logger.error(f"フォールバック送信も失敗: {e2}")

    async def _progress_notifier(self, channel, cancel_event: asyncio.Event):
        """長時間処理の進捗通知を送信するバックグラウンドタスク。

        Args:
            channel: 通知先チャンネル
            cancel_event: キャンセル用イベント
        """
        try:
            # 30秒待機
            await asyncio.sleep(30)
            if cancel_event.is_set():
                return
            await channel.send("⏳ 処理中です...（30秒経過）")

            # さらに60秒待機（計90秒）
            await asyncio.sleep(60)
            if cancel_event.is_set():
                return
            await channel.send(
                "⏳ まだ処理しています...（90秒経過。"
                "複雑な処理のため時間がかかっています）"
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"進捗通知エラー（無視）: {e}")

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
            embed = build_approval_embed(approval_result, content)
            view = ApprovalView(approval_result, self.approval, content)

            await self._safe_reply(
                reply_func if not defer_func else reply_func,
                channel,
                embed=embed,
                view=view,
            )

            # ユーザーの決定を待つ
            decision = await view.wait_for_decision()

            if decision in ("deny", "timeout"):
                if decision == "timeout":
                    try:
                        await channel.send(
                            "⚡ 承認がタイムアウトしました。操作をキャンセルします。"
                        )
                    except Exception:
                        pass
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

        # 許可ツール取得
        allowed_tools = self.approval.get_allowed_tools(
            approval_result.risk_level
        )

        # 進捗通知タスクを開始
        cancel_progress = asyncio.Event()
        progress_task = asyncio.create_task(
            self._progress_notifier(channel, cancel_progress)
        )

        try:
            # プロファイル自動判定に委ねる（max_turns明示指定なし）
            result = await self.claude.ask(
                content,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
            )
        finally:
            # 進捗通知を停止
            cancel_progress.set()
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

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

            for chunk in chunks:
                await self._safe_reply(reply_func, channel, content=chunk)
        else:
            error_msg = f"⚠️ エラーが発生しました: {result['error']}"
            # エラーもログに記録
            self.conversation.add_bot_response(
                content=f"[ERROR] {result['error']}",
                risk_level=approval_result.risk_level,
            )
            await self._safe_reply(reply_func, channel, content=error_msg)

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

        # Phase 3: 自己修復キーワード検知
        repair_cog = self.bot.get_cog("SelfRepair")
        if repair_cog and repair_cog.is_repair_request(content):
            logger.info("自己修復リクエスト検知 → SelfRepairService に委譲")
            async with message.channel.typing():
                result = await repair_cog.repair_service.attempt_repair(
                    trigger="user_request"
                )
                msg = result["message"]
                if len(msg) > 1900:
                    msg = msg[:1900] + "\n..."
                await message.reply(msg)
            return

        async with message.channel.typing():
            await self._process_with_approval(
                content=content,
                channel=message.channel,
                reply_func=message.reply,
            )


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(ClaudeBridge(bot))
