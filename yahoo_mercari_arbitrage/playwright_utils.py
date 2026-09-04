"""
Yahoo!オークション相場取得（Playwright版）
- Selenium(WebDriverフラグ等)は検出されやすいため microsoft/playwright + 軽量ステルスに移行
- ブラウザ/コンテキストは1回起動して使い回す（毎回の起動コストとブロック検出リスクを削減）
- playwright未インストール環境（例: 依存関係を絞った旧環境）でも動くようフォールバックする
"""
import re
import statistics
from urllib.parse import quote

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# navigator.webdriver 等の自動化検出フラグを消す軽量ステルス（playwright-stealth相当を自前実装）
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


class YahooAuctionFetcher:
    """Yahoo!オークションの売買相場をPlaywrightで取得する。"""

    def __init__(self, price_min=500, price_max=500000):
        self._pw = None
        self._browser = None
        self._context = None
        self.price_min = price_min
        self.price_max = price_max

    async def start(self):
        if not PLAYWRIGHT_AVAILABLE:
            return False
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = await self._browser.new_context(
            user_agent=_UA, locale="ja-JP", viewport={"width": 1280, "height": 900}
        )
        await self._context.add_init_script(_STEALTH_INIT_SCRIPT)
        return True

    async def fetch(self, model):
        if not model or not self._context:
            return {"median": None, "count": 0}
        url = f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
        page = await self._context.new_page()
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            for sel in ["span[class*='Price']", "span[class*='price']"]:
                els = await page.query_selector_all(sel)
                if not els:
                    continue
                prices = []
                for el in els[:20]:
                    raw = await el.inner_text()
                    txt = re.sub(r"[^\d]", "", raw or "")
                    if txt and self.price_min <= int(txt) <= self.price_max:
                        prices.append(int(txt))
                if prices:
                    return {"median": round(statistics.median(prices)), "count": len(prices), "prices": prices}
            return {"median": None, "count": 0}
        except Exception:
            return {"median": None, "count": 0}
        finally:
            await page.close()

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
