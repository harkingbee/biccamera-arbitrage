"""
Yahoo Shopping → Mercari 転売利益スキャナー
"""

import csv
import json
import logging
import os
import re
import statistics
import time
import traceback
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests

# Selenium は利用可能な場合のみインポート
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
CONFIG = {
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE": 0.10,       # 通常時10%（SPUや5のつく日で変動）
    "COUPON_DISCOUNT": 1000,         # 1000円OFFクーポン
    "PAYPAY_RATE": 0.01,             # PayPay基本1%
    "MIN_PROFIT_MARGIN": 0.15,       # 利益率15%以上を優先表示
    "MERCARI_SELLING_FEE": 0.10,     # メルカリ販売手数料10%
    "MERCARI_SHIPPING_COST": 600,    # 送料概算（大型商品は要変更）
    "MIN_PRICE": 20000,              # 仕入れ最低価格
    "API_RETRY_MAX": 3,              # APIリトライ最大回数
    "MERCARI_SLEEP_SEC": 2.5,        # メルカリリクエスト間隔（秒）
    "MERCARI_ITEMS": 20,             # 売り切れ取得件数
}

TODAY = datetime.now().strftime("%Y%m%d")
RESULTS_DIR = "results"
LOGS_DIR = "logs"


# ──────────────────────────────────────────────
# ロギング設定
# ──────────────────────────────────────────────
def setup_logger() -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"scraping_{TODAY}.log")

    logger = logging.getLogger("arbitrage")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


logger = setup_logger()


# ──────────────────────────────────────────────
# ステップ1: Yahoo Shopping ランキング取得
# ──────────────────────────────────────────────
def fetch_yahoo_ranking(app_id: str, price_from: int = 20000, results: int = 50) -> list:
    """
    Yahoo Shopping V3 itemSearch APIでランキング上位を取得する。
    sort=-review_count（レビュー数多い順）で売れ筋を近似。
    失敗時は指数バックオフで最大3回リトライ。
    """
    url = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    # query は必須。「送料無料」で全カテゴリを広く網羅（2,000万件以上）
    # sort=-sold は非存在のため -review_count（レビュー数多＝売れ筋の近似）を使用
    params = {
        "appid": app_id,
        "query": "送料無料",
        "price_from": price_from,
        "sort": "-review_count",
        "in_stock": "true",
        "results": results,
    }

    for attempt in range(1, CONFIG["API_RETRY_MAX"] + 1):
        try:
            logger.info(f"Yahoo API リクエスト (試行 {attempt}/{CONFIG['API_RETRY_MAX']})")
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("hits", [])
            logger.info(f"取得件数: {len(items)}")
            return [_parse_yahoo_item(i, rank + 1) for rank, i in enumerate(items)]
        except requests.RequestException as e:
            logger.warning(f"Yahoo API エラー: {e}")
            if attempt < CONFIG["API_RETRY_MAX"]:
                wait = 2 ** attempt
                logger.info(f"{wait}秒後にリトライ...")
                time.sleep(wait)
            else:
                logger.error("Yahoo API 取得失敗。空リストを返します。")
                return []


def _parse_yahoo_item(item: dict, rank: int) -> dict:
    price = item.get("price", 0)
    point = item.get("point", {})
    # 2025年2月以降の現行フィールド（PayPayポイント期間限定）
    point_times = (
        point.get("lyLimitedBonusTimes", 0)
        or point.get("lyLimitedPremiumBonusTimes", 0)
        or point.get("premiumBonusTimes", 0)
    )
    point_rate = point_times / 100.0 if point_times > 1 else 0.0

    return {
        "rank": rank,
        "name": item.get("name", ""),
        "price": price,
        "seller_name": item.get("seller", {}).get("name", ""),
        "url": item.get("url", ""),
        "point_rate": point_rate,
        "category": item.get("genreCategory", {}).get("name", ""),
        "review_rate": item.get("review", {}).get("rate", None),
        "review_count": item.get("review", {}).get("count", 0),
    }


# ──────────────────────────────────────────────
# ステップ2: 実質仕入れコスト計算
# ──────────────────────────────────────────────
def calculate_net_cost(
    price: float,
    yahoo_point_rate: float,
    coupon_discount: float,
    paypay_rate: float = 0.01,
) -> dict:
    """
    price           : 商品定価 (円)
    yahoo_point_rate: PayPayポイント還元率（例: 0.10 = 10%還元）
    coupon_discount : クーポン割引額 (円)
    paypay_rate     : PayPay残高払い基本還元率
    """
    price_after_coupon = max(price - coupon_discount, 0)
    point_return = price_after_coupon * yahoo_point_rate
    paypay_return = price_after_coupon * paypay_rate
    net_cost = price_after_coupon - point_return - paypay_return

    return {
        "price_after_coupon": price_after_coupon,
        "point_return_yen": round(point_return),
        "paypay_return_yen": round(paypay_return),
        "net_cost": round(net_cost),
        "discount_rate_total": round((price - net_cost) / price * 100, 2) if price else 0,
    }


# ──────────────────────────────────────────────
# ステップ3: メルカリ相場価格取得（Selenium）
# ──────────────────────────────────────────────
def _build_mercari_keyword(name: str) -> str:
    """商品名から型番・ブランド名を優先してキーワードを生成する。"""
    # 英数字の型番パターンを優先抽出
    model_pattern = re.findall(r"[A-Z0-9\-]{4,}", name.upper())
    if model_pattern:
        return " ".join(model_pattern[:2])
    # 型番がなければ先頭20文字
    return name[:20].strip()


def build_driver() -> "webdriver.Chrome":
    """headless Chrome ドライバーを返す（webdriver-manager対応）。"""
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium がインストールされていません: pip install selenium")

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts
        )
    except Exception:
        # webdriver-manager が使えない場合はデフォルトで試みる
        driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)
    return driver


def get_mercari_median_price(keyword: str, driver, n: int = 20) -> Optional[dict]:
    """
    メルカリの売り切れ商品から中央値・平均値・最低・最高価格を返す。
    取得失敗時は None を返す。
    """
    encoded = quote(keyword)
    url = f"https://www.mercari.com/jp/search/?keyword={encoded}&status=sold_out"

    try:
        driver.get(url)
        # merPrice クラスが出るまで待機（最大10秒）
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.merPrice, [class*='priceContainer']"))
        )
    except Exception:
        time.sleep(3)

    try:
        # 2025年現在のメルカリ価格クラス: merPrice / priceContainer__XXXXX
        PRICE_SELECTORS = [
            "span.merPrice",
            "span[class*='priceContainer']",
            "span[class*='price']",
            "[data-testid='price']",
        ]
        price_elements = []
        for sel in PRICE_SELECTORS:
            price_elements = driver.find_elements(By.CSS_SELECTOR, sel)
            if price_elements:
                break

        prices = []
        for el in price_elements[:n]:
            # "¥12,800" や "12800" など複数形式に対応
            text = re.sub(r"[^\d]", "", el.text)
            if text and 100 <= int(text) <= 10_000_000:
                prices.append(int(text))

        if not prices:
            logger.warning(f"メルカリ価格取得0件: {keyword}")
            return None

        return {
            "median": round(statistics.median(prices)),
            "mean": round(statistics.mean(prices)),
            "min": min(prices),
            "max": max(prices),
            "count": len(prices),
        }

    except Exception as e:
        logger.warning(f"メルカリスクレイピングエラー [{keyword}]: {e}")
        return None


# ──────────────────────────────────────────────
# ステップ4: 利益計算・判定
# ──────────────────────────────────────────────
def calculate_profit(net_cost: float, mercari_median_price: float) -> dict:
    """
    net_cost            : 実質仕入れコスト (円)
    mercari_median_price: メルカリ中央値価格 (円)
    """
    mercari_fee = mercari_median_price * CONFIG["MERCARI_SELLING_FEE"]
    shipping = CONFIG["MERCARI_SHIPPING_COST"]
    profit = mercari_median_price - mercari_fee - shipping - net_cost
    profit_margin = (profit / mercari_median_price * 100) if mercari_median_price else 0

    return {
        "mercari_price": round(mercari_median_price),
        "mercari_fee": round(mercari_fee),
        "shipping_cost": shipping,
        "profit_yen": round(profit),
        "profit_margin_pct": round(profit_margin, 2),
        "is_profitable": profit > 0 and profit_margin >= CONFIG["MIN_PROFIT_MARGIN"] * 100,
    }


# ──────────────────────────────────────────────
# ステップ5: CSV・サマリー出力
# ──────────────────────────────────────────────
CSV_COLUMNS = [
    "rank", "name", "category",
    "yahoo_price", "coupon_discount", "point_return",
    "net_cost", "mercari_median", "mercari_min",
    "profit_yen", "profit_margin_pct", "is_profitable",
    "yahoo_url",
]


def save_csv(rows: list) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"ranking_profit_{TODAY}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"CSV保存: {path}")
    return path


def save_summary(rows: list, started_at: str) -> str:
    profitable = [r for r in rows if r.get("is_profitable")]
    ratio = len(profitable) / len(rows) * 100 if rows else 0

    top5_profit = sorted(profitable, key=lambda r: r.get("profit_yen", 0), reverse=True)[:5]
    top5_margin = sorted(profitable, key=lambda r: r.get("profit_margin_pct", 0), reverse=True)[:5]

    lines = [
        "=" * 60,
        "  Yahoo → Mercari 転売スキャン サマリーレポート",
        "=" * 60,
        f"調査実施日時  : {started_at}",
        f"調査商品数    : {len(rows)} 件",
        f"利益あり件数  : {len(profitable)} 件 ({ratio:.1f}%)",
        "",
        "【利益額TOP5】",
    ]
    for i, r in enumerate(top5_profit, 1):
        lines.append(f"  {i}. {r['name'][:40]}  ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%)")

    lines += ["", "【利益率TOP5】"]
    for i, r in enumerate(top5_margin, 1):
        lines.append(f"  {i}. {r['name'][:40]}  {r['profit_margin_pct']}% (¥{r['profit_yen']:,})")

    lines += ["", "=" * 60]
    text = "\n".join(lines)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"summary_{TODAY}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"サマリー保存: {path}")
    return text


def print_profitable(rows: list) -> None:
    profitable = sorted(
        [r for r in rows if r.get("is_profitable")],
        key=lambda r: r.get("profit_margin_pct", 0),
        reverse=True,
    )
    if not profitable:
        print("\n利益ありの商品は見つかりませんでした。")
        return

    print("\n" + "=" * 70)
    print(f"  利益あり商品ランキング（利益率順）  計{len(profitable)}件")
    print("=" * 70)
    for i, r in enumerate(profitable, 1):
        print(
            f"[{i:02d}] {r['name'][:36]}\n"
            f"     仕入: ¥{r['net_cost']:>7,}  メルカリ相場: ¥{r['mercari_median']:>7,}"
            f"  利益: ¥{r['profit_yen']:>6,} ({r['profit_margin_pct']}%)\n"
            f"     URL: {r['yahoo_url']}\n"
        )


# ──────────────────────────────────────────────
# ステップ6: メイン処理
# ──────────────────────────────────────────────
def main() -> None:
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== スキャン開始 ===")

    # 1. Yahoo API でランキング取得
    items = fetch_yahoo_ranking(
        app_id=CONFIG["YAHOO_APP_ID"],
        price_from=CONFIG["MIN_PRICE"],
        results=50,
    )
    if not items:
        logger.error("商品が取得できませんでした。APP_IDを確認してください。")
        return

    # 2. 価格フィルタ（APIパラメータで指定済みだが念のため）
    items = [it for it in items if it["price"] >= CONFIG["MIN_PRICE"]]
    logger.info(f"フィルタ後: {len(items)} 件")

    # 3. 実質仕入れコスト計算
    for it in items:
        cost_info = calculate_net_cost(
            price=it["price"],
            yahoo_point_rate=it.get("point_rate") or CONFIG["YAHOO_POINT_RATE"],
            coupon_discount=CONFIG["COUPON_DISCOUNT"],
            paypay_rate=CONFIG["PAYPAY_RATE"],
        )
        it.update(cost_info)

    # 4. Selenium 起動 → メルカリ相場取得
    driver = None
    if SELENIUM_AVAILABLE:
        try:
            driver = build_driver()
            logger.info("Selenium (headless Chrome) 起動成功")
        except Exception as e:
            logger.error(f"Selenium 起動失敗: {e}\nメルカリ取得をスキップします。")

    rows = []
    for it in items:
        row = {
            "rank": it["rank"],
            "name": it["name"],
            "category": it["category"],
            "yahoo_price": it["price"],
            "coupon_discount": CONFIG["COUPON_DISCOUNT"],
            "point_return": it.get("point_return_yen", 0),
            "net_cost": it.get("net_cost", it["price"]),
            "mercari_median": None,
            "mercari_min": None,
            "profit_yen": None,
            "profit_margin_pct": None,
            "is_profitable": False,
            "yahoo_url": it["url"],
        }

        if driver:
            keyword = _build_mercari_keyword(it["name"])
            logger.info(f"[{it['rank']:02d}] メルカリ検索: {keyword}")
            mercari_data = get_mercari_median_price(keyword, driver, n=CONFIG["MERCARI_ITEMS"])
            time.sleep(CONFIG["MERCARI_SLEEP_SEC"])

            if mercari_data:
                row["mercari_median"] = mercari_data["median"]
                row["mercari_min"] = mercari_data["min"]
                profit_info = calculate_profit(row["net_cost"], mercari_data["median"])
                row.update({
                    "profit_yen": profit_info["profit_yen"],
                    "profit_margin_pct": profit_info["profit_margin_pct"],
                    "is_profitable": profit_info["is_profitable"],
                })
            else:
                logger.info(f"  → メルカリ取得失敗、スキップ: {it['name'][:30]}")
        else:
            logger.info(f"[{it['rank']:02d}] Seleniumなし → メルカリスキップ: {it['name'][:30]}")

        rows.append(row)

    # 5. 出力
    save_csv(rows)
    summary_text = save_summary(rows, started_at)
    print(summary_text)
    print_profitable(rows)

    # 6. Selenium 終了
    if driver:
        driver.quit()
        logger.info("Selenium 終了")

    logger.info("=== スキャン完了 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error("予期しないエラーが発生しました:\n" + traceback.format_exc())
