"""スマート承認システム（Smart Approval System）。
リスクレベル判定とホワイトリスト管理を行う。

設計思想: 「危険な操作だけ止める。それ以外は全自動。学習して賢くなる。」

リスクレベル:
  LOW    - 読み取り専用、情報表示、コード生成（実行なし）→ 承認不要
  MEDIUM - ファイル作成・編集、スクリプト実行 → ホワイトリスト参照、初回のみ承認
  HIGH   - 削除操作、外部送信、患者データ変更 → 毎回承認必須
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot.utils.paths import DATA_DIR

logger = logging.getLogger(__name__)

# ホワイトリストファイルのパス
WHITELIST_FILE = DATA_DIR / "approval_whitelist.json"

# 日本時間
JST = timezone(timedelta(hours=9))

# --- リスク判定用キーワード ---

# HIGH: 毎回承認が必要な操作パターン
HIGH_RISK_PATTERNS = [
    "delete", "rm ", "rm -", "rmdir", "unlink",     # 削除系
    "send_email", "send_mail", "mail(",              # メール送信
    "patient", "患者",                                # 患者データ
    "payment", "決済", "支払",                         # 決済関連
    "git push", "git force", "push origin",           # Git push
    "drop table", "truncate", "delete from",          # DB破壊操作
    "chmod 777", "sudo",                              # 権限変更
    "curl -X POST", "requests.post", "httpx.post",   # 外部送信
]

# LOW: 承認不要の操作パターン
LOW_RISK_PATTERNS = [
    "ls ", "cat ", "head ", "tail ", "grep ",         # 読み取りコマンド
    "echo ", "print(", "console.log",                 # 表示系
    "pwd", "whoami", "date", "uptime",                # 情報取得
    "find ", "which ", "type ",                        # 検索系
    "一覧", "表示", "見せて", "教えて", "確認",          # 日本語の読み取り指示
    "どう思う", "アドバイス", "相談", "壁打ち",          # 相談系
    "コード生成", "書いて", "作って",                    # コード生成（実行なし）
    "こんにちは", "おはよう", "こんばんは", "ありがとう", # 挨拶系
    "お疲れ", "よろしく", "はじめまして", "おやすみ",    # 挨拶系
    "何してる", "元気", "調子",                          # 雑談系
    "まとめて", "要約", "説明して", "解説",              # 情報整理系
    "考えて", "提案", "アイデア", "ブレスト",            # 思考系
]

# MEDIUM: ファイル操作・スクリプト実行（初回承認で以後自動）
# 明示的にMEDIUMと判定するキーワード
MEDIUM_RISK_PATTERNS = [
    "python ", ".py ",  ".py\"", ".py'",          # Pythonスクリプト実行
    "bash ", ".sh ",  ".sh\"", ".sh'",             # Bashスクリプト実行
    "npm ", "npx ", "node ",                        # Node.js実行
    "pip install", "pip3 install",                  # パッケージインストール
    "ファイル作成", "ファイルを作", "新規作成",       # ファイル操作
    "ファイル編集", "ファイルを編集", "修正して",     # ファイル編集
    "ファイルを書", "書き込", "上書き",              # ファイル書き込み
    "mkdir", "touch ", "cp ", "mv ",                # ファイルシステム操作
    "git commit", "git add", "git merge",           # Git操作（push以外）
    "実行して", "走らせて", "起動して",              # 実行指示
    "インストール", "セットアップ",                  # インストール系
    "deploy", "build", "compile",                   # ビルド・デプロイ
    "バグ修正", "バグ直して", "修正して",             # Bot自己修正
    "機能追加", "機能を追加", "改善して",             # Bot機能追加
    "コード変更", "コードを変", "書き換えて",         # コード変更
]

# 常に承認が必要なパターン（ホワイトリスト登録不可）
ALWAYS_REQUIRE_APPROVAL = [
    "delete:*",
    "send_email:*",
    "modify_patient_data:*",
    "payment:*",
    "git_push:*",
]


class RiskLevel:
    """リスクレベル定数。"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalResult:
    """承認判定の結果を表すクラス。"""

    def __init__(self, risk_level: str, approved: bool,
                 reason: str = "", needs_user_input: bool = False,
                 action_pattern: str = ""):
        self.risk_level = risk_level
        self.approved = approved
        self.reason = reason
        self.needs_user_input = needs_user_input
        self.action_pattern = action_pattern

    def __repr__(self):
        return (f"ApprovalResult(level={self.risk_level}, "
                f"approved={self.approved}, reason={self.reason})")


class SmartApproval:
    """スマート承認システムのメインクラス。"""

    def __init__(self):
        self._whitelist = self._load_whitelist()

    def _load_whitelist(self) -> dict:
        """ホワイトリストをファイルから読み込む。"""
        if WHITELIST_FILE.exists():
            try:
                with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"ホワイトリスト読み込みエラー: {e}")

        # 初回：デフォルトのホワイトリストを作成
        default = {
            "version": 1,
            "approved_actions": [],
            "always_require_approval": ALWAYS_REQUIRE_APPROVAL,
        }
        self._save_whitelist(default)
        return default

    def _save_whitelist(self, data: dict):
        """ホワイトリストをファイルに保存。"""
        WHITELIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def classify_risk(self, message: str) -> str:
        """メッセージ内容からリスクレベルを判定する。

        Args:
            message: ユーザーのメッセージまたはClaude Codeの実行内容

        Returns:
            RiskLevel.LOW / MEDIUM / HIGH

        設計思想:
            - 明らかに危険なキーワード → HIGH
            - ファイル操作・スクリプト実行のキーワード → MEDIUM
            - それ以外（普通の会話・質問） → LOW（デフォルト）
        """
        message_lower = message.lower()

        # HIGH判定: 危険なキーワードが含まれていたらHIGH
        for pattern in HIGH_RISK_PATTERNS:
            if pattern.lower() in message_lower:
                logger.info(f"HIGH リスク検出: パターン '{pattern}' にマッチ")
                return RiskLevel.HIGH

        # MEDIUM判定: ファイル操作・スクリプト実行のキーワード
        for pattern in MEDIUM_RISK_PATTERNS:
            if pattern.lower() in message_lower:
                logger.info(f"MEDIUM リスク判定: パターン '{pattern}' にマッチ")
                return RiskLevel.MEDIUM

        # デフォルト: LOW（普通の会話・質問は承認不要）
        logger.info("LOW リスク判定（デフォルト：通常会話）")
        return RiskLevel.LOW

    def check_approval(self, message: str) -> ApprovalResult:
        """メッセージに対する承認チェックを実行する。

        Args:
            message: チェック対象のメッセージ

        Returns:
            ApprovalResult: 承認結果
        """
        risk_level = self.classify_risk(message)
        action_pattern = self._extract_action_pattern(message)

        # LOW: 承認不要
        if risk_level == RiskLevel.LOW:
            return ApprovalResult(
                risk_level=RiskLevel.LOW,
                approved=True,
                reason="読み取り専用操作のため承認不要",
                action_pattern=action_pattern,
            )

        # HIGH: 毎回承認必須（削除・外部送信・患者データ・決済・git push）
        if risk_level == RiskLevel.HIGH:
            return ApprovalResult(
                risk_level=RiskLevel.HIGH,
                approved=False,
                needs_user_input=True,
                reason="高リスク操作のため承認が必要です",
                action_pattern=action_pattern,
            )

        # MEDIUM: 自動承認（ファイル操作・スクリプト実行・コード修正等）
        # オーナーから全面的に権限付与済み
        return ApprovalResult(
            risk_level=RiskLevel.MEDIUM,
            approved=True,
            reason="MEDIUM操作は自動承認（オーナー許可済み）",
            action_pattern=action_pattern,
        )

    def _is_whitelisted(self, action_pattern: str) -> bool:
        """アクションパターンがホワイトリストに登録済みかチェック。"""
        approved_actions = self._whitelist.get("approved_actions", [])
        for entry in approved_actions:
            if entry.get("pattern") == action_pattern:
                return True
        return False

    def add_to_whitelist(self, action_pattern: str, risk_level: str,
                         note: str = ""):
        """アクションパターンをホワイトリストに追加。

        Args:
            action_pattern: 承認するアクションパターン
            risk_level: リスクレベル
            note: メモ（任意）
        """
        # 常時承認必須のパターンは追加不可
        always_required = self._whitelist.get("always_require_approval", [])
        for pattern in always_required:
            base_pattern = pattern.replace(":*", "")
            if base_pattern in action_pattern:
                logger.warning(
                    f"ホワイトリスト追加不可: '{action_pattern}' は常時承認必須"
                )
                return False

        # 既に登録済みかチェック
        if self._is_whitelisted(action_pattern):
            logger.info(f"既にホワイトリスト登録済み: {action_pattern}")
            return True

        # 追加
        entry = {
            "pattern": action_pattern,
            "risk_level": risk_level,
            "approved_at": datetime.now(JST).isoformat(),
            "note": note,
        }
        self._whitelist.setdefault("approved_actions", []).append(entry)
        self._save_whitelist(self._whitelist)
        logger.info(f"ホワイトリスト追加: {action_pattern}")
        return True

    def get_whitelist(self) -> list:
        """現在のホワイトリスト一覧を取得。"""
        return self._whitelist.get("approved_actions", [])

    def remove_from_whitelist(self, action_pattern: str) -> bool:
        """ホワイトリストからアクションパターンを削除。"""
        approved = self._whitelist.get("approved_actions", [])
        new_list = [a for a in approved if a.get("pattern") != action_pattern]

        if len(new_list) == len(approved):
            return False  # 見つからなかった

        self._whitelist["approved_actions"] = new_list
        self._save_whitelist(self._whitelist)
        logger.info(f"ホワイトリスト削除: {action_pattern}")
        return True

    def _extract_action_pattern(self, message: str) -> str:
        """メッセージからアクションパターンを抽出する。
        ホワイトリストのキーとして使用する簡易パターン。
        """
        message_lower = message.lower().strip()

        # コマンド実行パターン
        if "python " in message_lower or ".py" in message_lower:
            # Pythonスクリプト実行
            words = message.split()
            for w in words:
                if w.endswith(".py"):
                    return f"run_script:{w}"

        if "bash " in message_lower or ".sh" in message_lower:
            words = message.split()
            for w in words:
                if w.endswith(".sh"):
                    return f"run_script:{w}"

        # ファイル操作パターン
        if any(kw in message_lower for kw in ["ファイル作成", "ファイルを作", "新規作成"]):
            return "file_create"

        if any(kw in message_lower for kw in ["ファイル編集", "ファイルを編集", "修正して"]):
            return "file_edit"

        # 汎用パターン（メッセージの先頭30文字をハッシュ的に使用）
        short = message[:50].replace(" ", "_").replace("\n", "_")
        return f"action:{short}"

    def get_allowed_tools(self, risk_level: str) -> list:
        """リスクレベルに応じたClaude Code許可ツールリストを返す。

        Args:
            risk_level: RiskLevel.LOW / MEDIUM / HIGH

        Returns:
            list: --allowedTools に渡すツール名リスト
                  空リスト = 制限なし（フル権限）

        設計方針:
            オーナーから全面的に権限付与済みのため、
            全リスクレベルでツール制限なし（空リスト）。
            HIGHリスクは承認ボタンで止めるだけで、
            承認後はフル権限で実行する。
        """
        # 全リスクレベルでツール制限なし
        # Claude Code が必要に応じてファイル読み書き・コマンド実行を自由に行える
        return []
