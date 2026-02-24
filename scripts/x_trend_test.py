"""
X トレンド収集テストスクリプト v2
- wait_until="domcontentloaded" + 手動wait
- headless=True（表示なし）
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

DATA_DIR = Path("/Users/shuta/sakana-bot/data")


async def get_trends_no_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print("X.com Exploreページにアクセス中...")
        try:
            await page.goto(
                "https://x.com/explore/tabs/trending",
                wait_until="domcontentloaded",
                timeout=20000
            )
        except Exception as e:
            print(f"goto エラー（続行）: {e}")

        print("ページ読み込み待機中（5秒）...")
        await page.wait_for_timeout(5000)

        title = await page.title()
        print(f"ページタイトル: {title}")

        # 現在のURL確認
        current_url = page.url
        print(f"現在のURL: {current_url}")

        # ページのHTMLを一部確認
        html = await page.content()
        print(f"HTMLサイズ: {len(html)}文字")
        print("HTML先頭500文字:")
        print(html[:500])

        # スクリーンショット保存
        screenshot_path = DATA_DIR / "x_trends_screenshot.png"
        await page.screenshot(path=str(screenshot_path))
        print(f"スクリーンショット保存: {screenshot_path}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(get_trends_no_login())
