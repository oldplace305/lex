"""Bot設定。.envファイルから環境変数を読み込む。"""
import os
from dotenv import load_dotenv
from bot.utils.paths import PROJECT_ROOT

# .envファイルを読み込み
load_dotenv(PROJECT_ROOT / ".env")

# Discord設定
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Bot設定
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")

# Claude Code CLI設定
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "120"))  # 秒
CLAUDE_OAUTH_TOKEN = os.getenv("CLAUDE_OAUTH_TOKEN", "")

# 定期報告チャンネル（0 = DMにフォールバック）
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "0"))

# ログ設定
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
