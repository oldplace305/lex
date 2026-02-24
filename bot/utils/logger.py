"""ロギング設定。ファイル出力 + コンソール出力。"""
import logging
import sys
from bot.utils.paths import LOGS_DIR


def setup_logging(level: str = "INFO"):
    """ロギングを初期化する。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ファイルハンドラ
    file_handler = logging.FileHandler(
        LOGS_DIR / "sakana-bot.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # コンソールハンドラ
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level, logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
