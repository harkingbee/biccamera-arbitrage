"""
IIJmio会員限定オンラインストア（閉店セール）専用 高精度アービトラージ
- GitHub精度向上版のロジックをIIJmioに移植
- PlaywrightでIIJmio closed_sale.htmlをログイン取得 → mercapi + YahooAuctionで保守的中央値
- atushi1841/margin_analyzer のトークン正規化・SKU一致・アクセサリ除外 + TF-IDF類似度で誤爆防止
- GitHub Actions 毎日12:00 JST実行想定、Discord通知は保守的中央値 + 件数閾値 + 信頼度つき
"""
import asyncio
import csv
import logging
import os
import re
import statistics
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote

try:
    from yahoo_mercari_arbitrage.mercapi_utils import fetch_mercapi_median
    from yahoo_mercari_arbitrage.playwright_utils import YahooAuctionFetcher
except ImportError:
    try:
        from mercapi_utils import fetch_mercapi_median
        from playwright_utils import YahooAuctionFetcher
    except ImportError:
        fetch_mercapi_median = None
        YahooAuctionFetcher = None

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    for ch in ("\u2212", "\uff0d", "\u2010", "\u2011"):
        s = s.replace(ch, "-")
    return s.lower()

def _tokens(s: str) -> set:
    s = _norm(s)
    subs = {
        "ソニー": "sony", "ミラーレス": "mirrorless", "一眼": "camera", "カメラ": "camera",
        "ボディ": "body", "中古": "", "美品": "", "未開封": "", "付属": "", "セット": "set",
        "レンズ": "lens", "スマホ": "smartphone", "スマートフォン": "smartphone",
        "アイフォン": "iphone", "アイホン": "iphone", "ギャラクシー": "galaxy",
        "ピクセル": "pixel", "エクスペリア": "xperia", "アクオス": "aquos",
        "レッドミー": "redmi", "モトローラ": "motorola", "オッポ": "oppo",
        "ケース": "case", "カバー": "cover", "フィルム": "film", "ガラス": "glass",
    }
    for ja, en in subs.items():
        s = s.replace(ja, en)
    toks = set(re.findall(r"[a-z0-9]{2,}", s))
    stop = {"the", "and", "for", "with", "new", "used", "jp", "yen", "gb", "sim", "free"}
    return toks - stop

def _match_score(t1, t2):
    a, b = _tokens(t1), _tokens(t2)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))

_ACCESSORY_MARKERS = [
    "ケース", "カバー", "フィルム", "ガラス", "保護", "充電", "ケーブル", "アダプタ",
    "スタンド", "ホルダー", "イヤホン", "ヘッドホン", "スピーカー", "バッテリー",
    "モバイルバッテリー", "充電器", "ストラップ", "リング", "三脚"
]

def _is_accessory(t1, t2):
    n1, n2 = _norm(t1), _norm(t2)
    return any(m in n1 or m in n2 for m in _ACCESSORY_MARKERS)

def _model_code(s):
    m = re.search(r"([A-Z]{2,}\-?[A-Z0-9]+[\-]?[A-Z0-9]+)", s.upper())
    return m.group(1) if m else ""

def _same_sku(t1, t2):
    c1, c2 = _model_code(t1), _model_code(t2)
    if c1 and c2:
        return c1 == c2
    return True

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    TFIDF_AVAILABLE = True
except ImportError:
    TFIDF_AVAILABLE = False

def _tfidf_score(target_title, candidate_titles):
    if not TFIDF_AVAILABLE or not candidate_titles:
        return 0.0
    try:
        vec = TfidfVectorizer(token_pattern=r"[a-zA-Z0-9]+", lowercase=True)
        docs = [_norm(target_title)] + [_norm(t) for t in candidate_titles]
        mat = vec.fit_transform(docs)
        sims = cosine_similarity(mat[0:1], mat[1:]).flatten()
        return float(max(sims)) if len(sims) else 0.0
    except Exception:
        return 0.0

CONFIG = {
    "MIN_PROFIT_MARGIN": 0.10,
    "MIN_COUNT": 3,
    "MERCARI_FEE": 0.10,
    "SHIPPING": 660,
    "SHIPPING_LARGE": 900,
    "MIN_PRICE": 5000,
    "MAX_PRODUCTS": 30,
    "TFIDF_THRESHOLD": 0.30,
    "MATCH_THRESHOLD": 0.35,
}

def get_shipping(name, category=""):
    text = (name or "") + (category or "")
    if any(k in text for k in ["iPad", "タブレット", "Apple Watch", "ノートパソコン"]):
        return CONFIG["SHIPPING_LARGE"]
    return CONFIG["SHIPPING"]

IIJMIO_LOGIN_URL = "https://www.iijmio.jp/auth/login/"
IIJMIO_CLOSED_SALE_URL = "https://www.iijmio.jp/campaign/auth/closed_sale.html"

TODAY = datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR = "results"
LOGS_DIR = "logs"

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

logger = logging.getLogger("iijmio_closed")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh = logging.FileHandler(os.path.join(LOGS_DIR, f"iijmio_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.handlers = []
logger.addHandler(fh)
logger.addHandler(ch)

_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime:{}};
"""

async def fetch_iijmio_products(email, password, max_products=30):
    if not PLAYWRIGHT_AVAILABLE:
        logger.error("playwright not installed: pip install playwright && playwright install chromium")
        return []
    if not email or not password:
        logger.error("IIJMIO_EMAIL / IIJMIO_PASSWORD が未設定")
        return []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="ja-JP", viewport={"width": 1280, "height": 900}
    )
    await ctx.add_init_script(_STEALTH_INIT)
    page = await ctx.new_page()

    try:
        logger.info("IIJmioログインページへ遷移")
        await page.goto(IIJMIO_LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        email_selectors = ['input[name="email"]', 'input[type="email"]', '#email', 'input[name*="mail"]', 'input[id*="email"]', 'input[autocomplete*="email"]']
        pwd_selectors = ['input[name="password"]', 'input[type="password"]', '#password', 'input[id*="pass"]']

        email_filled = False
        for sel in email_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(email)
                    email_filled = True
                    logger.info(f" email filled: {sel}")
                    break
            except Exception:
                continue
        if not email_filled:
            await page.evaluate('val => { document.querySelectorAll("input").forEach(el=>{ if(el.type==="email"||el.name?.includes("mail")||el.id?.includes("mail")) el.value=val }) }', email)
            logger.info(" email filled via evaluate fallback")

        pwd_filled = False
        for sel in pwd_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(password)
                    pwd_filled = True
                    logger.info(f" password filled: {sel}")
                    break
            except Exception:
                continue
        if not pwd_filled:
            await page.evaluate('val => { document.querySelectorAll("input").forEach(el=>{ if(el.type==="password") el.value=val }) }', password)
            logger.info(" password filled via evaluate fallback")

        submit_selectors = ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("ログイン")', 'a:has-text("ログイン")', '.btn-login', 'text=ログイン']
        clicked = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click()
                    clicked = True
                    logger.info(f" login clicked: {sel}")
                    break
            except Exception:
                continue
        if not clicked:
            await page.evaluate('document.querySelector("form")?.submit()')
            logger.info(" form submit via evaluate")

        await page.wait_for_timeout(4000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        url = page.url
        logger.info(f" ログイン後URL: {url}")

        logger.info(" 閉店セールページへ遷移")
        await page.goto(IIJMIO_CLOSED_SALE_URL, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        try:
            await page.screenshot(path=os.path.join(RESULTS_DIR, f"iijmio_closed_{TODAY}.png"), full_page=True)
        except Exception:
            pass

        content = await page.content()
        if "JavaScriptが無効" in content:
            logger.warning(" JS無効警告が残っている")

        products = await page.evaluate('''() => {
            const results = [];
            const selectors = [
                ".product-item", ".item-box", "[class*='product-item']", "[class*='item-box']",
                "li[class*='product']", "div[class*='product']", ".p-product", ".c-card",
                "[data-product]", "article", ".goods-item"
            ];
            let items = [];
            for (const sel of selectors) {
                const found = document.querySelectorAll(sel);
                if (found.length >= 2) { items = found; break; }
            }
            if (items.length === 0) {
                const priceEls = Array.from(document.querySelectorAll("*")).filter(el => {
                    const t = el.textContent || "";
                    return /¥\\s*[\\d,]+/.test(t) && t.length < 80;
                });
                const seen = new Set();
                for (const el of priceEls) {
                    const card = el.closest("li, div, article, section");
                    if (card && !seen.has(card)) { seen.add(card); items.push(card); }
                }
            }
            items.forEach((item, idx) => {
                try {
                    const nameEl = item.querySelector(".product-name, .item-name, h3, h4, [class*='name'], [class*='title'], a");
                    const priceEl = item.querySelector(".price, .sale-price, [class*='price'], .amount, [class*='Price']");
                    const linkEl = item.querySelector("a[href]");
                    const imgEl = item.querySelector("img");
                    const name = (nameEl?.textContent || item.textContent || "").trim().slice(0, 200);
                    const priceText = (priceEl?.textContent || item.textContent || "").trim();
                    const url = linkEl?.getAttribute("href") || "";
                    const imageUrl = imgEl?.getAttribute("src") || imgEl?.getAttribute("data-src") || "";
                    let price = 0;
                    const m = priceText.match(/[\\d,]{4,}/);
                    if (m) {
                        const v = parseInt(m[0].replace(/,/g, ""), 10);
                        if (v >= 1000 && v <= 500000) price = v;
                    }
                    if (name.length < 5 || price === 0) return;
                    if (name.includes("転売") || name.includes("注意事項") || name.includes("JavaScript")) return;
                    results.push({
                        name: name.split("\\n")[0].trim(),
                        price: price,
                        url: url.startsWith("http") ? url : (url ? "https://www.iijmio.jp" + url : "https://www.iijmio.jp/campaign/auth/closed_sale.html"),
                        imageUrl: imageUrl,
                        raw: priceText.slice(0,80)
                    });
                } catch(e) {}
            });
            return results;
        }''')

        dedup = {}
        for p in products:
            key = p["name"][:30]
            if key not in dedup or p["price"] < dedup[key]["price"]:
                dedup[key] = p
        products = [v for v in dedup.values() if v["price"] >= CONFIG["MIN_PRICE"]]
        products = sorted(products, key=lambda x: x["price"])[:max_products]

        logger.info(f" IIJmio商品 {len(products)}件取得")
        for p in products:
            logger.info(f"  - {p['name'][:40]} ¥{p['price']:,} {p['url'][:60]}")

        with open(os.path.join(RESULTS_DIR, f"iijmio_closed_{TODAY}.html"), "w", encoding="utf-8") as f:
            f.write(content[:500000])

        return products

    except Exception as e:
        logger.error(f" fetch_iijmio error: {e}", exc_info=True)
        try:
            await page.screenshot(path=os.path.join(RESULTS_DIR, f"iijmio_error_{TODAY}.png"), full_page=True)
        except Exception:
            pass
        return []
    finally:
        await browser.close()
        await pw.stop()


def extract_model(name: str):
    upper = name.upper()
    m = re.search(r"IPHONE\s*(\d+[A-Z]*)\s*(PRO|MAX|MINI|PLUS)?", upper)
    if m:
        return f"IPHONE{m.group(1)}{m.group(2) or ''}".replace(" ", "")
    m = re.search(r"PIXEL\s*(\d+[A-Z]*)", upper)
    if m:
        return f"PIXEL{m.group(1)}"
    cands = re.findall(r"[A-Z0-9\-]{4,}", upper)
    noise = {"IIJMIO", "YAHOO", "WHITE", "BLACK", "GB", "SIM", "FREE", "128GB", "256GB", "64GB"}
    cands = [c for c in cands if c not in noise and len(c) >= 4]
    if cands:
        cands.sort(key=len, reverse=True)
        hyphen = [c for c in cands if "-" in c]
        if hyphen:
            return hyphen[0]
        return cands[0]
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        return " ".join(words[:3])
    return name[:20].strip()

def calc_net(price: int, point_rate: float = 0.0):
    return {"net_cost": price}

async def main():
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== IIJmio閉店セール 高精度アービトラージ開始 ===")
    logger.info(f" CONFIG: margin{CONFIG['MIN_PROFIT_MARGIN']*100}% count>={CONFIG['MIN_COUNT']} tfidf>={CONFIG['TFIDF_THRESHOLD']}")

    email = os.environ.get("IIJMIO_EMAIL", "")
    password = os.environ.get("IIJMIO_PASSWORD", "")
    if not email:
        logger.warning(" IIJMIO_EMAIL 未設定（GitHub Secretsに設定してください）")

    products = await fetch_iijmio_products(email, password, max_products=CONFIG["MAX_PRODUCTS"])

    if not products:
        logger.error(" IIJmio商品0件。ログイン失敗またはセレクタ不一致の可能性。")
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if webhook:
            import requests as req
            try:
                req.post(webhook, json={"content": f"⚠️ IIJmio閉店セール: 商品0件（{started}）ログイン失敗またはページ構造変更の可能性。"}, timeout=10)
            except Exception:
                pass
        return

    for p in products:
        p.update(calc_net(p["price"]))
        p["model"] = extract_model(p["name"])
        p["shipping"] = get_shipping(p["name"])

    try:
        import importlib
        mu = importlib.import_module("yahoo_mercari_arbitrage.mercapi_utils")
        fetch_mercapi = mu.fetch_mercapi_median
    except Exception:
        try:
            import mercapi_utils as mu2
            fetch_mercapi = mu2.fetch_mercapi_median
        except Exception as e:
            logger.error(f" mercapi_utils import failed: {e}")
            fetch_mercapi = None

    auction = None
    auction_ready = False
    if YahooAuctionFetcher:
        auction = YahooAuctionFetcher(price_min=1000, price_max=500000)
        auction_ready = await auction.start()
        logger.info(f" YahooAuctionFetcher ready={auction_ready}")
    else:
        logger.warning(" YahooAuctionFetcher not available")

    rows = []
    csv_path = os.path.join(RESULTS_DIR, f"iijmio_{TODAY}.csv")
    cols = [
        "rank", "name", "model", "iijmio_price", "net_cost", "shipping",
        "mercapi_median", "mercapi_count", "mercapi_total_found",
        "yahooAuction_median", "yahooAuction_count",
        "conservative_median", "match_score", "tfidf_score", "is_same_sku", "is_accessory",
        "profit_yen", "profit_margin", "is_profitable", "confidence", "total_count",
        "iijmio_url"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()

    for idx, p in enumerate(products, 1):
        model = p["model"]
        logger.info(f"[{idx:02d}] {p['name'][:40]} model={model} ¥{p['price']:,}")

        m_data = None
        if fetch_mercapi and model:
            try:
                m_data = await fetch_mercapi(model)
            except Exception as e:
                logger.warning(f"  mercapi error {model}: {e}")
                m_data = {"median": None, "count": 0}
            await asyncio.sleep(0.8)

        y_data = {"median": None, "count": 0}
        if auction_ready and model:
            try:
                y_data = await auction.fetch(model)
            except Exception as e:
                logger.warning(f"  yahoo auction error {model}: {e}")

        m_med = (m_data or {}).get("median")
        y_med = y_data.get("median")
        if m_med and y_med:
            cons = min(m_med, y_med)
        elif m_med:
            cons = m_med
        elif y_med:
            cons = y_med
        else:
            cons = None

        score = _match_score(p["name"], model or "") if model else 0
        same_sku = _same_sku(p["name"], model or "") if model else True
        is_acc = _is_accessory(p["name"], model or "") if model else False

        tfidf = 0.0
        if TFIDF_AVAILABLE and m_data and m_data.get("prices") is not None:
            candidate_titles = m_data.get("titles", [model] if model else [])
            tfidf = _tfidf_score(p["name"], candidate_titles)
        else:
            tfidf = score

        ship = p["shipping"]
        profit = margin = None
        is_prof = False
        conf = "none"
        total = (m_data["count"] if m_data else 0) + (y_data["count"] if y_data else 0)

        passed_filter = (
            cons is not None
            and not is_acc
            and same_sku
            and score >= CONFIG["MATCH_THRESHOLD"]
            and total >= CONFIG["MIN_COUNT"]
        )

        if cons and passed_filter:
            fee = cons * CONFIG["MERCARI_FEE"]
            profit = cons - fee - ship - p["net_cost"]
            margin = profit / cons * 100 if cons else 0
            is_prof = profit > 0 and margin >= CONFIG["MIN_PROFIT_MARGIN"] * 100
            conf = "high" if total >= 8 else ("medium" if total >= 4 else "low")
            if tfidf < CONFIG["TFIDF_THRESHOLD"]:
                conf += "_tfidf_low"
                if tfidf < 0.20:
                    is_prof = False
                    conf = "tfidf_filtered"
        elif is_acc:
            conf = "accessory_filtered"
        elif not same_sku:
            conf = "sku_mismatch"
        elif score < CONFIG["MATCH_THRESHOLD"]:
            conf = f"low_match_{score:.2f}"
        elif total < CONFIG["MIN_COUNT"]:
            conf = f"few_samples_{total}"
        elif cons is None:
            conf = "no_market_data"

        row = {
            "rank": idx, "name": p["name"], "model": model,
            "iijmio_price": p["price"], "net_cost": p["net_cost"], "shipping": ship,
            "mercapi_median": m_med, "mercapi_count": (m_data or {}).get("count", 0),
            "mercapi_total_found": (m_data or {}).get("total_found", 0) or (m_data or {}).get("count", 0),
            "yahooAuction_median": y_med, "yahooAuction_count": y_data.get("count", 0),
            "conservative_median": cons,
            "match_score": round(score, 3), "tfidf_score": round(tfidf, 3),
            "is_same_sku": same_sku, "is_accessory": is_acc,
            "profit_yen": round(profit) if profit is not None else "",
            "profit_margin": round(margin, 2) if margin is not None else "",
            "is_profitable": is_prof, "confidence": conf, "total_count": total,
            "iijmio_url": p["url"]
        }
        rows.append(row)
        with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writerow(row)

        logger.info(
            f" -> cons{cons} m{m_med}({(m_data or {}).get('count',0)}) y{y_med}({y_data.get('count',0)}) "
            f"score{score:.2f} tfidf{tfidf:.2f} sku{same_sku} acc{is_acc} profit{row['profit_yen']} conf{conf}"
        )
        await asyncio.sleep(0.4)

    if auction_ready and auction:
        await auction.stop()

    prof = [r for r in rows if r["is_profitable"]]
    filtered = [r for r in rows if not r["is_profitable"]]

    lines = [
        "=" * 64,
        "  IIJmio閉店セール 高精度アービトラージ サマリー",
        "=" * 64,
        f"実施: {started}",
        f"取得: {len(products)}件 / 対象: {len(rows)}件",
        f"利益あり: {len(prof)}件 / 除外: {len(filtered)}件",
        f"条件: 利益率{CONFIG['MIN_PROFIT_MARGIN']*100:.0f}%+ 件数{CONFIG['MIN_COUNT']}件+ 保守的中央値 + SKU/アクセサリ/TF-IDFフィルタ",
        "",
        "【利益あり TOP5】",
    ]
    for i, r in enumerate(sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:5], 1):
        lines.append(
            f"  {i}. {r['name'][:40]} ¥{r['profit_yen']:,} ({r['profit_margin']}%) "
            f"model={r['model']} cons{ r['conservative_median']} m{r['mercapi_median']}x{r['mercapi_count']} y{r['yahooAuction_median']}x{r['yahooAuction_count']} {r['confidence']}"
        )
    if not prof:
        lines += ["  なし（参考: 損失小さい順TOP3）"]
        for r in sorted([x for x in rows if isinstance(x["profit_yen"], int)], key=lambda x: x["profit_yen"], reverse=True)[:3]:
            lines.append(f"  {r['name'][:36]} 利益{r['profit_yen']} net{r['net_cost']} cons{r['conservative_median']} {r['confidence']}")

    lines += ["", "【除外理由 TOP】"]
    from collections import Counter
    cnt = Counter([r["confidence"] for r in filtered])
    for k, v in cnt.most_common(5):
        lines.append(f"  {k}: {v}件")

    lines += ["", "=" * 64]
    txt = "\n".join(lines)
    print(txt)

    summary_path = os.path.join(RESULTS_DIR, f"iijmio_summary_{TODAY}.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(txt)
    logger.info(f"CSV {csv_path} Summary {summary_path}")

    if prof:
        target_path = os.path.join(RESULTS_DIR, f"iijmio_target_{TODAY}.csv")
        with open(target_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["rank", "product_name", "model", "iijmio_price", "net_cost", "profit_yen", "profit_margin", "confidence", "iijmio_url"])
            w.writeheader()
            for i, r in enumerate(sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True), 1):
                w.writerow({"rank": i, "product_name": r["name"], "model": r["model"], "iijmio_price": r["iijmio_price"], "net_cost": r["net_cost"], "profit_yen": r["profit_yen"], "profit_margin": r["profit_margin"], "confidence": r["confidence"], "iijmio_url": r["iijmio_url"]})
        print(f"対象リスト {target_path}")
        import shutil
        shutil.copy(target_path, os.path.join(RESULTS_DIR, "iijmio_target_latest.csv"))
        shutil.copy(csv_path, os.path.join(RESULTS_DIR, "iijmio_latest.csv"))
        shutil.copy(summary_path, os.path.join(RESULTS_DIR, "iijmio_summary_latest.txt"))

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if webhook:
        import requests as req
        try:
            if prof:
                desc_lines = []
                for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:5]:
                    desc_lines.append(
                        f"**{r['name'][:38]}**\n"
                        f" model `{r['model']}` IIJmio ¥{r['iijmio_price']:,} → 相場 ¥{r['conservative_median']:,} (保守的)\n"
                        f" 利益 **¥{r['profit_yen']:,}** ({r['profit_margin']}%) 信頼度 `{r['confidence']}` 件数{r['total_count']} [商品ページ]({r['iijmio_url']})"
                    )
                content = (
                    f"## 📱 IIJmio閉店セール 利益あり {len(prof)}件 ({started})\n"
                    + "\n\n".join(desc_lines)
                    + f"\n\nCSV: `{os.path.basename(csv_path)}` / 対象: `{len(prof)}件`"
                )
            else:
                content = (
                    f"## 📱 IIJmio閉店セール チェック完了 ({started})\n"
                    f"取得 {len(products)}件 / 対象 {len(rows)}件 / 利益あり **0件**\n"
                    f"除外内訳: {dict(Counter([r['confidence'] for r in filtered]).most_common(3))}\n"
                    f"CSV: `{os.path.basename(csv_path)}`"
                )
            if len(content) > 1900:
                content = content[:1900] + "\n...(省略)"
            r = req.post(webhook, json={"content": content}, timeout=15)
            logger.info(f" Discord通知 {r.status_code}")
            if r.status_code not in (200, 204):
                logger.warning(f" Discord失敗 {r.text[:500]}")
        except Exception as e:
            logger.warning(f" Discord error {e}")

    logger.info("=== 完了 ===")

if __name__ == "__main__":
    asyncio.run(main())
