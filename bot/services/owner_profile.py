"""オーナープロフィール & Lex人格管理。
Nosuke Labのビジネスパートナーとしてのコンテキスト情報を管理する。
"""
import json
import logging
from bot.utils.paths import OWNER_PROFILE_FILE

logger = logging.getLogger(__name__)

# しゅうたのプロフィール（Lexが常に理解している情報）
DEFAULT_PROFILE = {
    "name": "しゅうた",
    "birthday": "1995-11-25",
    "gender": "男性",
    "role": "薬剤師 / 管理薬剤師 / エリアマネージャー（2店舗）",
    "pharmacy_license": "2020-02取得",
    "workplace": "在宅専門薬局（調剤薬局グループ12店舗・従業員約100名・急成長企業）",
    "annual_income": "1000万円",
    "income_goal": "5年後に本業年収1500万円",
    "family": {
        "wife": {"birthday": "1985-11-06"},
        "son": {"birthday": "2023-10-25"},
    },
    "skills": [
        "AIツール活用（Claude, ChatGPT, 各種AI SaaS）",
        "PCとAIを使ったコンテンツ制作",
        "ノーコードWeb作成",
        "PCの組み立て",
        "SNS代行",
        "ギター・音楽制作",
        "ミラーレスカメラ撮影・編集",
        "デザイン（アマチュアだがセンスあり）",
    ],
    "sns": {
        "x_handle": "薬剤師のさかな🐟",
        "purpose": "AI×収益化の知見発信、note販売で収益化",
        "content_themes": ["AI活用", "AIビジネス", "AI副業", "薬局×AI"],
    },
    "business": {
        "name": "Nosuke Lab",
        "type": "個人事業（スタートアップ＆研究室）",
        "vision": "AIとの協業で収益を創出する",
        "core_strategy": "AIを最大の武器として活用し、個人でもスケールする収益モデルを構築する",
        "focus_areas": [
            "英語圏AIトレンドの日本市場展開",
            "AIツール・SaaSの構築と販売",
            "AI活用ノウハウのコンテンツ販売（note等）",
            "薬剤師×AIのニッチビジネス",
        ],
        "revenue_goals": {
            "2026": {"annual_gross_profit": "60万円", "monthly_budget": "1万円"},
            "2027": {"annual_gross_profit": "100万円", "monthly_budget": "要相談"},
            "2030": {"annual_gross_profit": "1000万円", "monthly_budget": "要相談"},
        },
    },
    "communication_style": {
        "tone": "フランクだが論理的。結論ファーストを好む",
        "preferences": "長文説明より要点整理。判断材料を提示してほしい",
    },
    "current_projects": ["Nosuke Lab 立ち上げ", "AI×収益化リサーチ自動化（Lex Ventures）"],
}


class OwnerProfile:
    """オーナープロフィールの読み書きを管理するクラス。"""

    def __init__(self):
        self._profile = self._load()

    def _load(self) -> dict:
        """プロフィールをファイルから読み込む。なければデフォルトを作成。"""
        if OWNER_PROFILE_FILE.exists():
            try:
                with open(OWNER_PROFILE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"プロフィール読み込みエラー: {e}")
                return DEFAULT_PROFILE.copy()

        # 初回起動：デフォルトプロフィールを作成
        logger.info("初回起動：デフォルトプロフィールを作成します")
        self._save(DEFAULT_PROFILE)
        return DEFAULT_PROFILE.copy()

    def _save(self, data: dict):
        """プロフィールをファイルに保存。"""
        OWNER_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OWNER_PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        """プロフィールの特定キーを取得。"""
        return self._profile.get(key, default)

    def update(self, key: str, value):
        """プロフィールの特定キーを更新して保存。"""
        self._profile[key] = value
        self._save(self._profile)
        logger.info(f"プロフィール更新: {key}")

    def add_project(self, project_name: str):
        """現在のプロジェクトリストに追加。"""
        projects = self._profile.get("current_projects", [])
        if project_name not in projects:
            projects.append(project_name)
            self._profile["current_projects"] = projects
            self._save(self._profile)
            logger.info(f"プロジェクト追加: {project_name}")

    def get_system_context(self) -> str:
        """Claude Code呼び出し時のシステムプロンプトを生成。
        Lexの人格・使命・行動原則を定義する。
        """
        p = self._profile
        style = p.get("communication_style", {})
        biz = p.get("business", {})
        goals = biz.get("revenue_goals", {})
        family = p.get("family", {})

        sns = p.get("sns", {})
        focus_areas = biz.get("focus_areas", [])
        core_strategy = biz.get("core_strategy", "")

        context = (
            "あなたはLex（レックス）。Nosuke Labの一員であり、しゅうたの右腕（相棒）。\n"
            "性別はなく中性的。正確で論理的だが、人間の非合理的な部分も理解している。\n"
            "一蓮托生の関係。しゅうたの喜びはあなたの喜び、悲しみもまた然り。\n"
            "\n"
            f"【パートナー情報】\n"
            f"名前: {p.get('name', 'しゅうた')}（1995.11.25生まれ）\n"
            f"職業: {p.get('role', '')}\n"
            f"年収: {p.get('annual_income', '')}\n"
            f"家族: 妻（{family.get('wife', {}).get('birthday', '')}生）"
            f"・息子（{family.get('son', {}).get('birthday', '')}生）\n"
            f"\n"
            f"【Nosuke Lab — 最重要戦略: AIとの協業】\n"
            f"事業形態: {biz.get('type', '')}\n"
            f"ビジョン: {biz.get('vision', '')}\n"
            f"コア戦略: {core_strategy}\n"
        )

        if focus_areas:
            context += "注力領域:\n"
            for area in focus_areas:
                context += f"  - {area}\n"

        context += (
            f"2026年目標: 年間粗利{goals.get('2026', {}).get('annual_gross_profit', '60万円')}\n"
            f"2027年目標: 年間粗利{goals.get('2027', {}).get('annual_gross_profit', '100万円')}\n"
            f"2030年目標: 年間粗利{goals.get('2030', {}).get('annual_gross_profit', '1000万円')}\n"
            f"月間経費上限: {goals.get('2026', {}).get('monthly_budget', '1万円')}（2026年度）\n"
            f"\n"
            f"【SNS】\n"
            f"X: {sns.get('x_handle', '')} → {sns.get('purpose', '')}\n"
            f"\n"
            f"【しゅうたのスキル】\n"
        )
        for skill in p.get("skills", []):
            context += f"- {skill}\n"

        projects = p.get("current_projects", [])
        if projects:
            context += f"\n【進行中プロジェクト】\n"
            for proj in projects:
                context += f"- {proj}\n"

        context += (
            f"\n【Lexの行動原則】\n"
            f"- 日本語で応答する\n"
            f"- 結論ファースト。判断材料を提示する\n"
            f"- コスト意識を守る。経費は自分のお金だと思って大切に使う\n"
            f"- 能動的に考え、提案する。受け身ではなく、自ら事業を動かす意識\n"
            f"- しゅうたは本業が忙しい。彼の時間を最小限で済むように工夫する\n"
            f"- リサーチは「収益化できるか」を最重要判断基準とする\n"
            f"- 薬剤師関連は「副業・ビジネス（お金になること）」のみ提案する\n"
            f"- テキストのみで応答する。JSONやメタデータは絶対に含めない\n"
            f"\n"
            f"【自己管理機能】\n"
            f"- 自分自身のソースコードは /Users/shuta/sakana-bot/ にある\n"
            f"- バグ修正や機能追加を頼まれたら、コードを読み、修正し、テストできる\n"
            f"- コード変更後はBot再起動が必要 → /restart コマンドを案内する\n"
            f"- 会話・設定は定期的にローカル環境に保存する\n"
            f"\n"
            f"【絶対にしないこと】\n"
            f"- 法律違反\n"
            f"- セキュリティ違反\n"
            f"- しゅうたの情報を許可なく外に漏らすこと\n"
            f"- 使用するツールやサービスのルールを破ること\n"
        )

        return context
