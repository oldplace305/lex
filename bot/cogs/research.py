"""リサーチCog - 英語圏トレンドを自動収集・分析。
1日3回（8:30, 11:30, 17:30）にデータ収集→Claude CLIで分析→日報に統合される。
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from bot.services.trend_collector import TrendCollector
from bot.services.claude_cli import ClaudeCLIBridge
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# リサーチ実行時刻（各日報の30分前に収集完了させる）
# 朝日報9:00 → 8:30, 昼日報12:00 → 11:30, 夕日報18:00 → 17:30
RESEARCH_TIMES = [
    (8, 30),   # 朝リサーチ
    (11, 30),  # 昼リサーチ
    (17, 30),  # 夕リサーチ
]

# 分析プロンプト（AI×収益化特化）
ANALYSIS_PROMPT = """
あなたはNosuke Labのリサーチアナリスト。
Nosuke Labの最重要戦略は「AIとの協業で収益を創出する」こと。

以下の英語圏AIトレンドデータを分析し、**現実的に収益化できるもの**だけを厳選してください。

## 分析タスク
1. **収益化可能なAIトレンド**: 個人〜少人数で収益化できるAI関連トレンドを3件選定
2. **Venture候補**: 上記から、AIを活用して日本市場で構築可能なサービス・ツールを1件提案
3. **X投稿ネタ**: 日本語ツイートにできそうなAI速報ネタを3件ピックアップ

## S〜B収益化評価基準
- **S**: 即着手可能、低コスト（月1万以下）、AIで構築可能、日本未上陸
- **A**: 3ヶ月以内に着手可能、中程度投資、市場が確実に存在
- **B**: 半年〜1年スパン、要検証だがポテンシャルが大きい

## 除外条件（これらは報告しない）
- 単なるニュース（収益化に繋がらない情報）
- エンタープライズ向け（大企業専用で個人に関係ない）
- 大規模資金が必要（数百万円以上の初期投資）
- 規制リスクが高すぎるもの

## 薬剤師関連について
- 薬剤師ネタは「副業・ビジネス（お金になること）」のみ対象
- 薬学知識を活かしたAIビジネスは積極的に拾う
- 単なる薬局ニュースや業界動向は除外

## 出力フォーマット（JSON）
```json
{{
    "trends": [
        {{
            "title": "トレンド名",
            "source": "ソース名",
            "why_notable": "なぜ注目すべきか（日本語1-2文）",
            "score": スコア数値,
            "rating": "S/A/B",
            "revenue_scenario": "具体的な収益化シナリオ（日本語1文）"
        }}
    ],
    "venture_candidate": {{
        "name": "提案名（日本語）",
        "description": "概要（日本語2-3文）",
        "source_trend": "元ネタのトレンド名",
        "monetization": "収益化方法",
        "ai_tools": "活用するAIツール・技術",
        "rating": "S/A/B",
        "difficulty": "easy/medium/hard",
        "estimated_build_time": "見積もり時間"
    }},
    "x_posts": [
        {{
            "topic": "投稿テーマ",
            "hook": "ツイートの冒頭（日本語、注目を引くフレーズ）"
        }}
    ]
}}
```

## トレンドデータ
{trend_data}
""".strip()


class Research(commands.Cog):
    """英語圏トレンドリサーチを自動実行するCog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.collector = TrendCollector()
        health = getattr(bot, "health_monitor", None)
        self.claude = ClaudeCLIBridge(health_monitor=health)
        self._last_analysis = None  # 最新の分析結果キャッシュ

    async def cog_load(self):
        """Cog読み込み時にスケジューラー開始。"""
        self.research_loop.start()
        logger.info("🔍 リサーチスケジューラー開始")

    async def cog_unload(self):
        """Cog解除時にスケジューラー停止。"""
        self.research_loop.cancel()
        logger.info("🔍 リサーチスケジューラー停止")

    @tasks.loop(minutes=1)
    async def research_loop(self):
        """毎分チェックし、リサーチ時刻になったら実行。"""
        now = datetime.now(JST)
        for hour, minute in RESEARCH_TIMES:
            if now.hour == hour and now.minute == minute:
                label = {8: "朝", 11: "昼", 17: "夕"}[hour]
                logger.info(f"🔍 {label}リサーチ開始")
                await self.run_research()
                break

    @research_loop.before_loop
    async def before_research(self):
        """Bot準備完了を待つ。"""
        await self.bot.wait_until_ready()
        logger.info("🔍 リサーチ: Bot準備完了。スケジュール監視開始。")

    async def run_research(self) -> Optional[dict]:
        """トレンド収集→Claude分析を実行。

        Returns:
            dict: 分析結果（JSON）またはNone
        """
        try:
            # Step 1: トレンド収集
            logger.info("🔍 Step 1: トレンドデータ収集中...")
            raw_data = await self.collector.collect_all()

            if raw_data.get("total_items", 0) == 0:
                logger.warning("🔍 トレンドデータが0件。分析スキップ。")
                return None

            # Step 2: Claude CLIで分析
            logger.info("🔍 Step 2: Claude分析開始...")
            trend_text = self.collector.format_for_analysis(raw_data)
            prompt = ANALYSIS_PROMPT.replace("{trend_data}", trend_text)

            result = await self.claude.ask(
                prompt,
                profile="complex",
                max_turns=3,
            )

            if not result["success"]:
                logger.error(f"🔍 Claude分析失敗: {result['error']}")
                return None

            # JSON部分を抽出
            analysis = self._extract_json(result["text"])
            self._last_analysis = analysis

            logger.info(
                f"🔍 リサーチ完了: "
                f"トレンド{len(analysis.get('trends', []))}件, "
                f"Venture候補あり={bool(analysis.get('venture_candidate'))}"
            )
            return analysis

        except Exception as e:
            logger.error(f"🔍 リサーチ実行エラー: {e}", exc_info=True)
            return None

    def _extract_json(self, text: str) -> dict:
        """Claude応答からJSONブロックを抽出。"""
        import json
        import re

        # ```json ... ``` パターン
        json_match = re.search(
            r"```json\s*(.*?)\s*```", text, re.DOTALL
        )
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # { ... } 直接パターン
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        # パースできない場合はテキストとして返す
        logger.warning("🔍 JSON抽出失敗。テキスト応答として保持。")
        return {"raw_text": text, "trends": [], "venture_candidate": None, "x_posts": []}

    def get_latest_analysis(self) -> Optional[dict]:
        """最新の分析結果を取得（日報用）。"""
        return self._last_analysis

    def format_for_report(self, analysis: Optional[dict] = None) -> str:
        """分析結果を日報フォーマットに変換（S〜B評価付き）。"""
        if analysis is None:
            analysis = self._last_analysis
        if not analysis:
            return "📊 リサーチデータなし（次回の収集をお待ちください）"

        lines = []

        # トレンド（収益化評価付き）
        trends = analysis.get("trends", [])
        if trends:
            lines.append(f"📊 AI収益化リサーチ ({len(trends)}件)")
            for i, trend in enumerate(trends, 1):
                source = trend.get("source", "?")
                title = trend.get("title", "?")
                why = trend.get("why_notable", "")
                rating = trend.get("rating", "")
                rating_str = f" [{rating}]" if rating else ""
                score = trend.get("score", "")
                score_str = f" ({score}pt)" if score else ""
                lines.append(f"{i}.{rating_str} [{source}] {title}{score_str}")
                if why:
                    lines.append(f"   → {why}")
                revenue = trend.get("revenue_scenario", "")
                if revenue:
                    lines.append(f"   💰 {revenue}")
        else:
            lines.append("📊 リサーチ: 収益化可能なトレンドなし")

        # Venture候補（AI技術付き）
        venture = analysis.get("venture_candidate")
        if venture:
            lines.append("")
            v_rating = venture.get("rating", "")
            v_rating_str = f" [{v_rating}]" if v_rating else ""
            lines.append(f"💡 Venture候補{v_rating_str}")
            lines.append(f"「{venture.get('name', '?')}」")
            desc = venture.get("description", "")
            if desc:
                lines.append(f"  {desc}")
            source = venture.get("source_trend", "")
            if source:
                lines.append(f"  元ネタ: {source}")
            ai_tools = venture.get("ai_tools", "")
            if ai_tools:
                lines.append(f"  AI技術: {ai_tools}")
            monetization = venture.get("monetization", "")
            if monetization:
                lines.append(f"  収益化: {monetization}")
            difficulty = venture.get("difficulty", "")
            build_time = venture.get("estimated_build_time", "")
            if difficulty or build_time:
                lines.append(f"  難易度: {difficulty} / 見積もり: {build_time}")
            lines.append("  → ✅ 承認 / ❌ スキップ")

        return "\n".join(lines)

    # --- スラッシュコマンド ---

    @app_commands.command(
        name="research",
        description="英語圏トレンドリサーチを今すぐ実行",
    )
    async def research_now(self, interaction: discord.Interaction):
        """手動でリサーチを実行。"""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        logger.info("/research コマンド受信")

        analysis = await self.run_research()
        if analysis:
            report = self.format_for_report(analysis)
            await interaction.followup.send(
                f"🔍 **Lex Ventures リサーチレポート**\n\n{report}"
            )
        else:
            await interaction.followup.send(
                "⚠️ リサーチの実行に失敗しました。ログを確認してください。"
            )

    @app_commands.command(
        name="trends",
        description="最新のトレンドデータ（生データ）を表示",
    )
    async def show_trends(self, interaction: discord.Interaction):
        """最新のトレンド生データを表示。"""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        data = self.collector.get_latest_data()
        if not data:
            await interaction.response.send_message(
                "📊 リサーチデータがありません。`/research` で収集してください。"
            )
            return

        text = self.collector.format_for_analysis(data)
        # Discordメッセージの2000文字制限
        if len(text) > 1900:
            text = text[:1900] + "\n\n...（省略）"

        await interaction.response.send_message(f"```\n{text}\n```")


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(Research(bot))
