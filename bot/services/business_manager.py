"""事業管理モジュール（Business Manager）。
Nosuke Labの売上・経費・収支を管理する。

data/business.json に以下を保持:
- transactions: 売上・経費の取引一覧
- monthly_summary: 月次集計キャッシュ
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot.utils.paths import DATA_DIR

logger = logging.getLogger(__name__)

# 事業データファイル
BUSINESS_FILE = DATA_DIR / "business.json"

# 日本時間
JST = timezone(timedelta(hours=9))


class BusinessManager:
    """Nosuke Labの事業収支を管理するクラス。"""

    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        """事業データを読み込む。"""
        if BUSINESS_FILE.exists():
            try:
                with open(BUSINESS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"business.json読み込みエラー: {e}")

        default = {
            "version": 1,
            "transactions": [],
        }
        self._save(default)
        return default

    def _save(self, data: dict = None):
        """事業データを保存。"""
        if data is None:
            data = self._data
        BUSINESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BUSINESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_transaction(self, tx_type: str, amount: int,
                        category: str, description: str = "") -> dict:
        """取引を追加。

        Args:
            tx_type: "income"（売上）or "expense"（経費）
            amount: 金額（円）正の整数
            category: カテゴリ
            description: メモ

        Returns:
            dict: 追加された取引
        """
        now = datetime.now(JST)
        tx = {
            "id": len(self._data["transactions"]) + 1,
            "type": tx_type,
            "amount": abs(amount),
            "category": category,
            "description": description,
            "date": now.strftime("%Y-%m-%d"),
            "month": now.strftime("%Y-%m"),
            "created_at": now.isoformat(),
        }
        self._data["transactions"].append(tx)
        self._save()
        logger.info(
            f"取引追加: {tx_type} ¥{amount:,} [{category}] {description}"
        )
        return tx

    def delete_transaction(self, tx_id: int) -> bool:
        """取引を削除。"""
        before = len(self._data["transactions"])
        self._data["transactions"] = [
            t for t in self._data["transactions"] if t.get("id") != tx_id
        ]
        if len(self._data["transactions"]) < before:
            self._save()
            logger.info(f"取引削除: ID={tx_id}")
            return True
        return False

    def get_monthly_summary(self, year_month: str = None) -> dict:
        """月次サマリーを取得。

        Args:
            year_month: "YYYY-MM" 形式。Noneなら当月。

        Returns:
            dict: {income, expense, profit, transactions, budget_remaining}
        """
        if year_month is None:
            year_month = datetime.now(JST).strftime("%Y-%m")

        txs = [
            t for t in self._data["transactions"]
            if t.get("month") == year_month
        ]

        income = sum(t["amount"] for t in txs if t["type"] == "income")
        expense = sum(t["amount"] for t in txs if t["type"] == "expense")

        # 月間予算（2026年: 1万円/月）
        year = int(year_month[:4])
        if year <= 2026:
            monthly_budget = 10000
        else:
            monthly_budget = 0  # 未設定

        return {
            "year_month": year_month,
            "income": income,
            "expense": expense,
            "profit": income - expense,
            "transaction_count": len(txs),
            "monthly_budget": monthly_budget,
            "budget_remaining": monthly_budget - expense if monthly_budget else None,
            "transactions": txs,
        }

    def get_yearly_summary(self, year: int = None) -> dict:
        """年次サマリーを取得。

        Args:
            year: 西暦。Noneなら当年。

        Returns:
            dict: {income, expense, profit, goal, progress_pct}
        """
        if year is None:
            year = datetime.now(JST).year

        year_str = str(year)
        txs = [
            t for t in self._data["transactions"]
            if t.get("month", "").startswith(year_str)
        ]

        income = sum(t["amount"] for t in txs if t["type"] == "income")
        expense = sum(t["amount"] for t in txs if t["type"] == "expense")
        profit = income - expense

        # 年間粗利目標
        goals = {2026: 600000, 2027: 1000000, 2030: 10000000}
        goal = goals.get(year, 0)
        progress_pct = round(profit / goal * 100, 1) if goal else 0

        return {
            "year": year,
            "income": income,
            "expense": expense,
            "profit": profit,
            "goal": goal,
            "progress_pct": progress_pct,
            "transaction_count": len(txs),
        }

    def get_recent_transactions(self, limit: int = 10) -> list:
        """直近の取引一覧を取得。"""
        return sorted(
            self._data["transactions"],
            key=lambda t: t.get("created_at", ""),
            reverse=True,
        )[:limit]

    def get_category_breakdown(self, year_month: str = None) -> dict:
        """カテゴリ別の内訳を取得。"""
        if year_month is None:
            year_month = datetime.now(JST).strftime("%Y-%m")

        txs = [
            t for t in self._data["transactions"]
            if t.get("month") == year_month
        ]

        income_by_cat = {}
        expense_by_cat = {}

        for t in txs:
            cat = t.get("category", "その他")
            amount = t["amount"]
            if t["type"] == "income":
                income_by_cat[cat] = income_by_cat.get(cat, 0) + amount
            else:
                expense_by_cat[cat] = expense_by_cat.get(cat, 0) + amount

        return {
            "year_month": year_month,
            "income_by_category": income_by_cat,
            "expense_by_category": expense_by_cat,
        }
