"""
ビックカメラグループ（コジマYahoo!店 y-kojima / ソフマップ Yahoo!店 y-sofmap）限定スキャン
Yahoo Shopping API → 実質仕入れコスト → メルカリ相場 → 利益判定
"""
import csv
import os
import re
import statistics
import time
import traceback
from datetime import datetime
from urllib.parse import quote
import requests

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

CONFIG = {
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE": 0.10,
    "COUPON_DISCOUNT": 1000,
    "PAYPAY_RATE": 0.01,
    "MIN_PROFIT_MARGIN": 0.15,
    "MERCARI_SELLING_FEE": 0.10,
    "MERCARI_SHIPPING_COST": 600,
    "MIN_PRICE": 20000,
    "API_RETRY_MAX": 3,
    "MERCARI_SLEEP_SEC": 2.5,
    "MERCARI_ITEMS": 20,
}

# ビックカメラグループのセラーID
BIC_SELLERS = ["y-sofmap", "y-kojima"]
# 広くカバーするクエリ（各セラーで分散取得して重複排除）
QUERIES = ["家電", "テレビ", "パソコン", "カメラ"]
RESULTS_PER_QUERY = 15

TODAY = datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR = "results"
LOGS_DIR = "logs"

import logging
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
logger = logging.getLogger("biccamera")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh = logging.FileHandler(os.path.join(LOGS_DIR, f"biccamera_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.handlers = []
logger.addHandler(fh)
logger.addHandler(ch)

def fetch_for_seller(seller_id, query, results=15):
    url = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    params = {
        "appid": CONFIG["YAHOO_APP_ID"],
        "query": query,
        "seller_id": seller_id,
        "price_from": CONFIG["MIN_PRICE"],
        "sort": "-review_count",
        "in_stock": "true",
        "results": results,
    }
    for attempt in range(1, CONFIG["API_RETRY_MAX"]+1):
        try:
            logger.info(f"Yahoo API seller={seller_id} query={query} try {attempt}")
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 10 * attempt
                logger.warning(f"429 rate limit, wait {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", [])
            logger.info(f"  -> {len(hits)}件 (total {data.get('totalResultsAvailable')})")
            return hits
        except Exception as e:
            logger.warning(f"  error: {e}")
            if attempt < CONFIG["API_RETRY_MAX"]:
                time.sleep(2**attempt)
            else:
                return []
    return []

def parse_hit(item, rank):
    price = item.get("price", 0)
    point = item.get("point", {})
    point_times = (point.get("lyLimitedBonusTimes", 0) or point.get("lyLimitedPremiumBonusTimes", 0) or point.get("premiumBonusTimes", 0))
    point_rate = point_times / 100.0 if point_times > 1 else 0.0
    # if no point info, use config default for display but keep 0 for calc fallback
    return {
        "rank": rank,
        "name": item.get("name",""),
        "price": price,
        "seller_name": item.get("seller",{}).get("name",""),
        "seller_id": item.get("seller",{}).get("sellerId",""),
        "url": item.get("url",""),
        "point_rate": point_rate,
        "category": item.get("genreCategory",{}).get("name",""),
        "review_count": item.get("review",{}).get("count",0),
        "code": item.get("code",""),
    }

def calculate_net_cost(price, yahoo_point_rate, coupon_discount, paypay_rate=0.01):
    price_after = max(price - coupon_discount, 0)
    point_return = price_after * yahoo_point_rate
    paypay_return = price_after * paypay_rate
    net_cost = price_after - point_return - paypay_return
    return {
        "price_after_coupon": price_after,
        "point_return_yen": round(point_return),
        "paypay_return_yen": round(paypay_return),
        "net_cost": round(net_cost),
        "discount_rate_total": round((price - net_cost)/price*100,2) if price else 0,
    }

def build_keyword(name):
    m = re.findall(r"[A-Z0-9\-]{4,}", name.upper())
    if m:
        return " ".join(m[:2])
    return name[:20].strip()

def build_driver():
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium not installed")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    except Exception:
        driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)
    return driver

def get_mercari_median(keyword, driver, n=20):
    encoded = quote(keyword)
    url = f"https://www.mercari.com/jp/search/?keyword={encoded}&status=sold_out"
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.merPrice, [class*='priceContainer']")))
    except Exception:
        time.sleep(3)
    try:
        selectors = ["span.merPrice","span[class*='priceContainer']","span[class*='price']","[data-testid='price']"]
        els=[]
        for sel in selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                break
        prices=[]
        for el in els[:n]:
            txt=re.sub(r"[^\d]","", el.text)
            if txt and 100 <= int(txt) <= 10000000:
                prices.append(int(txt))
        if not prices:
            return None
        return {"median": round(statistics.median(prices)), "mean": round(statistics.mean(prices)), "min": min(prices), "max": max(prices), "count": len(prices)}
    except Exception as e:
        logger.warning(f"mercari error {keyword}: {e}")
        return None

def calculate_profit(net_cost, mercari_median):
    fee = mercari_median * CONFIG["MERCARI_SELLING_FEE"]
    shipping = CONFIG["MERCARI_SHIPPING_COST"]
    profit = mercari_median - fee - shipping - net_cost
    margin = profit / mercari_median * 100 if mercari_median else 0
    return {"mercari_price": round(mercari_median), "mercari_fee": round(fee), "shipping_cost": shipping, "profit_yen": round(profit), "profit_margin_pct": round(margin,2), "is_profitable": profit>0 and margin >= CONFIG["MIN_PROFIT_MARGIN"]*100}

def main():
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== ビックカメラグループ スキャン開始 ===")
    all_hits=[]
    seen_codes=set()
    for seller in BIC_SELLERS:
        for q in QUERIES:
            hits = fetch_for_seller(seller, q, RESULTS_PER_QUERY)
            for h in hits:
                code = h.get("code","")
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                all_hits.append(h)
            time.sleep(3)  # rate limit対策
    logger.info(f"総取得（重複排除後）: {len(all_hits)}件")
    # parse
    items = [parse_hit(h,i+1) for i,h in enumerate(all_hits)]
    # price filter already, but double-check
    items = [it for it in items if it["price"] >= CONFIG["MIN_PRICE"]]
    logger.info(f"価格フィルタ後: {len(items)}件")
    # cost calc
    for it in items:
        rate = it.get("point_rate") or CONFIG["YAHOO_POINT_RATE"]
        # 実還元が0ならデフォルト10%を使う
        if rate == 0:
            rate = CONFIG["YAHOO_POINT_RATE"]
        cost = calculate_net_cost(it["price"], rate, CONFIG["COUPON_DISCOUNT"], CONFIG["PAYPAY_RATE"])
        it.update(cost)

    # 上位30件に絞ってメルカリ照合（時間対策）
    items = sorted(items, key=lambda x: x["review_count"], reverse=True)[:30]
    logger.info(f"メルカリ照合対象: {len(items)}件 (レビュー数上位30)")

    driver=None
    if SELENIUM_AVAILABLE:
        try:
            driver=build_driver()
            logger.info("Selenium 起動成功")
        except Exception as e:
            logger.error(f"Selenium 起動失敗: {e}")
    else:
        logger.warning("Selenium 未インストール → メルカリスキップ")

    rows=[]
    for it in items:
        row={
            "rank": it["rank"],
            "name": it["name"],
            "category": it["category"],
            "seller": it["seller_name"],
            "yahoo_price": it["price"],
            "coupon_discount": CONFIG["COUPON_DISCOUNT"],
            "point_return": it.get("point_return_yen",0),
            "net_cost": it.get("net_cost", it["price"]),
            "mercari_median": None,
            "mercari_min": None,
            "profit_yen": None,
            "profit_margin_pct": None,
            "is_profitable": False,
            "yahoo_url": it["url"],
        }
        if driver:
            kw = build_keyword(it["name"])
            logger.info(f"[{it['rank']:02d}] mercari: {kw} <- {it['name'][:30]}")
            data = get_mercari_median(kw, driver, n=CONFIG["MERCARI_ITEMS"])
            time.sleep(CONFIG["MERCARI_SLEEP_SEC"])
            if data:
                row["mercari_median"]=data["median"]
                row["mercari_min"]=data["min"]
                p=calculate_profit(row["net_cost"], data["median"])
                row.update({"profit_yen": p["profit_yen"], "profit_margin_pct": p["profit_margin_pct"], "is_profitable": p["is_profitable"]})
        rows.append(row)

    # 保存
    csv_cols=["rank","name","category","seller","yahoo_price","coupon_discount","point_return","net_cost","mercari_median","mercari_min","profit_yen","profit_margin_pct","is_profitable","yahoo_url"]
    csv_path=os.path.join(RESULTS_DIR, f"biccamera_profit_{TODAY}.csv")
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    logger.info(f"CSV保存 {csv_path}")

    # サマリー
    profitable=[r for r in rows if r.get("is_profitable")]
    ratio=len(profitable)/len(rows)*100 if rows else 0
    top_profit=sorted(profitable, key=lambda r: r.get("profit_yen",0), reverse=True)[:5]
    top_margin=sorted(profitable, key=lambda r: r.get("profit_margin_pct",0), reverse=True)[:5]
    lines=["="*60,"  ビックカメラグループ → メルカリ 転売スキャン","="*60,f"実施日時: {started}",f"調査商品数: {len(rows)}",f"利益あり: {len(profitable)}件 ({ratio:.1f}%)","","【利益額TOP5】"]
    for i,r in enumerate(top_profit,1):
        lines.append(f"  {i}. {r['name'][:40]} ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%)")
    lines+=["","【利益率TOP5】"]
    for i,r in enumerate(top_margin,1):
        lines.append(f"  {i}. {r['name'][:40]} {r['profit_margin_pct']}% (¥{r['profit_yen']:,})")
    lines+=["","="*60]
    txt="\n".join(lines)
    summary_path=os.path.join(RESULTS_DIR, f"biccamera_summary_{TODAY}.txt")
    with open(summary_path,"w",encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    # 利益あり詳細
    if profitable:
        print("\n"+"="*70)
        print(f"  利益あり商品（利益率順） 計{len(profitable)}件")
        print("="*70)
        for i,r in enumerate(sorted(profitable, key=lambda x: x["profit_margin_pct"], reverse=True),1):
            print(f"[{i:02d}] {r['name'][:36]}\n     仕入: ¥{r['net_cost']:,}  メルカリ: ¥{r['mercari_median']:,}  利益: ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%)\n     {r['yahoo_url']}\n")
    else:
        print("\n利益あり商品は見つかりませんでした。条件（ポイント10%+クーポン1000円+PayPay1% を想定）では利益率15%以上を満たす商品がありませんでした。")
        # それでも参考として利益額がマイナスが小さい順に表示
        print("\n--- 参考: 損失が小さい商品TOP5（仕入コストに対するメルカリ相場の近さ） ---")
        with_data=[r for r in rows if r.get("profit_yen") is not None]
        for r in sorted(with_data, key=lambda x: x["profit_yen"], reverse=True)[:5]:
            print(f"  {r['name'][:40]} 利益¥{r['profit_yen']:,}  仕入¥{r['net_cost']:,} vs メルカリ¥{r['mercari_median']:,} ({r['profit_margin_pct']}%)")

    if driver:
        driver.quit()
    logger.info("=== 完了 ===")
    print(f"\nCSV: {csv_path}\nSummary: {summary_path}")

if __name__=="__main__":
    try:
        main()
    except Exception:
        logger.error(traceback.format_exc())

