"""Lex Bot エントリーポイント。
python -m bot.main で起動する。

エラーハンドリング:
- 予期しないエラーで終了した場合、自動で再起動を試みる（最大3回）
- launchdのKeepAlive設定と連携して高い可用性を実現
"""
import sys
import time
import logging
from bot.config import DISCORD_TOKEN, LOG_LEVEL
from bot.lex_bot import LexBot
from bot.utils.logger import setup_logging

# 再起動の最大試行回数
MAX_RESTART_ATTEMPTS = 3
# 再起動間の待機時間（秒）
RESTART_DELAY = 10


def main():
    """Bot起動のメイン関数。エラー時は自動再起動。"""
    # ロギング初期化
    setup_logging(level=LOG_LEVEL)
    logger = logging.getLogger(__name__)

    # トークン確認
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN が .env ファイルに設定されていません！")
        logger.error(".env.example を参考に .env ファイルを作成してください。")
        sys.exit(1)

    attempts = 0

    while attempts < MAX_RESTART_ATTEMPTS:
        bot = LexBot()

        try:
            logger.info("⚡ Lex 起動中...")
            bot.run(DISCORD_TOKEN, log_handler=None)
            # bot.run() が正常に終了した場合（ユーザーによるシャットダウン）
            logger.info("Bot正常終了")
            break

        except KeyboardInterrupt:
            logger.info("Bot停止（ユーザーによる中断）")
            break

        except Exception as e:
            attempts += 1
            logger.error(
                f"Bot異常終了（{attempts}/{MAX_RESTART_ATTEMPTS}回目）: {e}",
                exc_info=True,
            )

            if attempts < MAX_RESTART_ATTEMPTS:
                logger.info(f"{RESTART_DELAY}秒後に再起動します...")
                time.sleep(RESTART_DELAY)
            else:
                logger.error(
                    f"最大再起動回数（{MAX_RESTART_ATTEMPTS}回）に達しました。"
                    f"手動で確認してください。"
                )
                sys.exit(1)


if __name__ == "__main__":
    main()
