"""
夏物在庫処分スキャン：ハンディファン中心 + 扇風機/ネッククーラー等
ビックカメラグループ y-sofmap / y-kojima
"""
import csv, os, re, statistics, time, traceback
from datetime import datetime
from urllib.parse import quote
import requests

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_AVAILABLE=True
except ImportError:
    SELENIUM_AVAILABLE=False

CONFIG={
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE": 0.10,
    "COUPON_DISCOUNT": 1000,
    "PAYPAY_RATE": 0.01,
    "MIN_PROFIT_MARGIN": 0.10,  # 夏物は薄利でもOKとするため10%に緩和
    "MERCARI_SELLING_FEE": 0.10,
    "MERCARI_SHIPPING_COST": 600,
    "MIN_PRICE": 2000,  # ハンディファンは2000-5000円帯が多いため
    "API_RETRY_MAX": 3,
    "MERCARI_SLEEP_SEC": 2.5,
    "MERCARI_ITEMS": 20,
}
BIC_SELLERS=["y-sofmap","y-kojima"]
# 夏物クエリ：在庫処分はキーワードではなく価格・在庫で判断するため商品ジャンルで網羅
QUERIES=[
    "ハンディファン",
    "扇風機",
    "ネッククーラー",
    "サーキュレーター",
    "除湿機",
    "アウトレット",
]
RESULTS_PER_QUERY=12
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"
LOGS_DIR="logs"
import logging
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("summer")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"summer_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
ch=logging.StreamHandler(); ch.setLevel(logging.INFO); ch.setFormatter(fmt)
logger.handlers=[]; logger.addHandler(fh); logger.addHandler(ch)

def fetch_for_seller(seller_id, query, results=12):
    url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    params={"appid":CONFIG["YAHOO_APP_ID"],"query":query,"seller_id":seller_id,"price_from":CONFIG["MIN_PRICE"],"sort":"-review_count","in_stock":"true","results":results}
    for attempt in range(1, CONFIG["API_RETRY_MAX"]+1):
        try:
            logger.info(f"Yahoo seller={seller_id} query={query} try{attempt}")
            resp=requests.get(url, params=params, timeout=15)
            if resp.status_code==429:
                w=10*attempt
                logger.warning(f"429 wait {w}s"); time.sleep(w); continue
            resp.raise_for_status()
            data=resp.json()
            hits=data.get("hits",[])
            logger.info(f" -> {len(hits)}件 total {data.get('totalResultsAvailable')}")
            return hits
        except Exception as e:
            logger.warning(f" error {e}")
            if attempt<CONFIG["API_RETRY_MAX"]:
                time.sleep(2**attempt)
            else:
                return []
    return []

def parse_hit(item, rank):
    price=item.get("price",0)
    point=item.get("point",{})
    times=(point.get("lyLimitedBonusTimes",0) or point.get("lyLimitedPremiumBonusTimes",0) or point.get("premiumBonusTimes",0))
    rate=times/100.0 if times>1 else 0.0
    # 在庫処分類の判定ヒント：商品名に訳あり/アウトレット/処分/在庫限り/箱不良 が含まれる or 価格がカテゴリ平均より安い等
    name=item.get("name","")
    clearance_keywords=["訳あり","アウトレット","処分","在庫限り","箱不良","外箱不良","展示品","在庫処分","セール"]
    is_clearance=any(k in name for k in clearance_keywords)
    return {"rank":rank,"name":name,"price":price,"seller_name":item.get("seller",{}).get("name",""),"seller_id":item.get("seller",{}).get("sellerId",""),"url":item.get("url",""),"point_rate":rate,"category":item.get("genreCategory",{}).get("name",""),"review_count":item.get("review",{}).get("count",0),"code":item.get("code",""),"is_clearance_flag":is_clearance}

def calc_net(price, rate, coupon, paypay=0.01):
    after=max(price-coupon,0)
    point=after*rate
    pay=after*paypay
    net=after-point-pay
    return {"price_after_coupon":after,"point_return_yen":round(point),"paypay_return_yen":round(pay),"net_cost":round(net),"discount_total":round((price-net)/price*100,2) if price else 0}

def build_keyword(name):
    # 型番優先、次にブランド+機種
    m=re.findall(r"[A-Z0-9\-]{4,}", name.upper())
    if m:
        # 型番が複数ある場合は最大2つ
        return " ".join(m[:2])
    # 型番なしは先頭20文字 + 夏物キーワード補強
    base=name[:20].strip()
    # ハンディファンの場合はブランド名を残す
    for kw in ["ハンディファン","扇風機","ネッククーラー","サーキュレーター"]:
        if kw in name:
            return kw+" "+base[:12]
    return base

def build_driver():
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium missing")
    opts=Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--window-size=1280,900")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        driver=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    except Exception:
        driver=webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)
    return driver

def get_mercari_median(keyword, driver, n=20):
    enc=quote(keyword)
    url=f"https://www.mercari.com/jp/search/?keyword={enc}&status=sold_out"
    try:
        driver.get(url)
        WebDriverWait(driver,7).until(EC.presence_of_element_located((By.CSS_SELECTOR,"span.merPrice, [class*='priceContainer']")))
    except Exception:
        time.sleep(2)
    try:
        sels=["span.merPrice","span[class*='priceContainer']","span[class*='price']","[data-testid='price']"]
        els=[]
        for sel in sels:
            els=driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                break
        prices=[]
        for el in els[:n]:
            txt=re.sub(r"[^\d]","",el.text)
            if txt and 100 <= int(txt) <= 10000000:
                prices.append(int(txt))
        if not prices:
            return None
        return {"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"min":min(prices),"max":max(prices),"count":len(prices)}
    except Exception as e:
        logger.warning(f"mercari err {keyword}: {e}")
        return None

def calc_profit(net, median):
    fee=median*CONFIG["MERCARI_SELLING_FEE"]
    ship=CONFIG["MERCARI_SHIPPING_COST"]
    # 安価商品は送料600でも比率が大きいため、軽量(ハンディファン)は400円で再計算も提示する
    profit=median-fee-ship-net
    margin=profit/median*100 if median else 0
    # ハンディファン向け小型配送想定（ゆうパケット 300円）での参考利益も
    profit_small=median-fee-300-net
    return {"mercari_price":round(median),"mercari_fee":round(fee),"shipping_cost":ship,"profit_yen":round(profit),"profit_margin_pct":round(margin,2),"is_profitable":profit>0 and margin>=CONFIG["MIN_PROFIT_MARGIN"]*100, "profit_small_post":round(profit_small)}

def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== 夏物在庫処分スキャン開始 ===")
    all_hits=[]; seen=set()
    for seller in BIC_SELLERS:
        for q in QUERIES:
            hits=fetch_for_seller(seller,q,RESULTS_PER_QUERY)
            for h in hits:
                code=h.get("code","")
                if code in seen: continue
                seen.add(code)
                all_hits.append(h)
            time.sleep(3)
    logger.info(f"重複排除後 {len(all_hits)}件")
    items=[parse_hit(h,i+1) for i,h in enumerate(all_hits)]
    items=[it for it in items if it["price"]>=CONFIG["MIN_PRICE"]]
    # ネットコスト計算
    for it in items:
        rate=it.get("point_rate") or CONFIG["YAHOO_POINT_RATE"]
        if rate==0: rate=CONFIG["YAHOO_POINT_RATE"]
        c=calc_net(it["price"], rate, CONFIG["COUPON_DISCOUNT"], CONFIG["PAYPAY_RATE"])
        it.update(c)
    # ハンディファン優先 + レビュー数 + 在庫処分フラグでソート
    # ハンディファンを上位に、次に在庫処分フラグ付きを優先
    def sort_key(x):
        is_handy=1 if "ハンディファン" in x["name"] or "ハンディ" in x["name"] else 0
        return (is_handy, x["is_clearance_flag"], x["review_count"])
    items_sorted=sorted(items, key=sort_key, reverse=True)
    # 上位35件をメルカリ照合
    target=items_sorted[:35]
    logger.info(f"メルカリ照合対象 {len(target)}件 (ハンディファン優先)")
    driver=None
    if SELENIUM_AVAILABLE:
        try:
            driver=build_driver(); logger.info("Selenium 起動成功")
        except Exception as e:
            logger.error(f"Selenium失敗 {e}")
    rows=[]
    for it in target:
        row={"rank":it["rank"],"name":it["name"],"category":it["category"],"seller":it["seller_name"],"yahoo_price":it["price"],"coupon_discount":CONFIG["COUPON_DISCOUNT"],"point_return":it.get("point_return_yen",0),"net_cost":it.get("net_cost",it["price"]),"is_clearance":it["is_clearance_flag"],"mercari_median":None,"mercari_min":None,"profit_yen":None,"profit_margin_pct":None,"is_profitable":False,"profit_small_post":None,"yahoo_url":it["url"]}
        if driver:
            kw=build_keyword(it["name"])
            logger.info(f"[{it['rank']:02d}] mercari kw={kw} | {it['name'][:32]}")
            data=get_mercari_median(kw, driver, n=CONFIG["MERCARI_ITEMS"])
            time.sleep(CONFIG["MERCARI_SLEEP_SEC"])
            if data:
                row["mercari_median"]=data["median"]; row["mercari_min"]=data["min"]
                p=calc_profit(row["net_cost"], data["median"])
                row.update({"profit_yen":p["profit_yen"],"profit_margin_pct":p["profit_margin_pct"],"is_profitable":p["is_profitable"],"profit_small_post":p["profit_small_post"]})
        rows.append(row)
    # 保存
    cols=["rank","name","category","seller","yahoo_price","coupon_discount","point_return","net_cost","is_clearance","mercari_median","mercari_min","profit_yen","profit_margin_pct","profit_small_post","is_profitable","yahoo_url"]
    csv_path=os.path.join(RESULTS_DIR, f"summer_handy_{TODAY}.csv")
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    logger.info(f"CSV {csv_path}")
    profitable=[r for r in rows if r.get("is_profitable")]
    positive=[r for r in rows if r.get("profit_yen") is not None and r["profit_yen"]>0]
    ratio=len(profitable)/len(rows)*100 if rows else 0
    top_profit=sorted(profitable, key=lambda r: r.get("profit_yen",0), reverse=True)[:5]
    top_margin=sorted(profitable, key=lambda r: r.get("profit_margin_pct",0), reverse=True)[:5]
    # ハンディファン限定フィルタ
    handy=[r for r in rows if "ハンディ" in r["name"] or "ハンディファン" in r["name"]]
    handy_profit=[r for r in handy if r.get("is_profitable")]
    handy_positive=[r for r in handy if r.get("profit_yen") and r["profit_yen"]>0]
    lines=["="*60,"  ビックカメラ夏物（ハンディファン中心）在庫処分スキャン","="*60,f"実施日時: {started}",f"調査対象: {len(rows)}件 (ハンディファン {len(handy)}件 含む)",f"利益あり(10%以上): {len(profitable)}件 ({ratio:.1f}%)",f"利益プラス(1円以上): {len(positive)}件", "", f"ハンディファン内 利益あり: {len(handy_profit)}件 / プラス: {len(handy_positive)}件", "", "【利益額TOP5 全体】"]
    for i,r in enumerate(top_profit,1):
        mark=" 在庫処分" if r["is_clearance"] else ""
        lines.append(f"  {i}. {r['name'][:38]} ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%){mark}")
    lines+=["","【利益率TOP5】"]
    for i,r in enumerate(top_margin,1):
        lines.append(f"  {i}. {r['name'][:38]} {r['profit_margin_pct']}% (¥{r['profit_yen']:,})")
    if not profitable:
        lines+=["", "(利益率10%未満のため TOP5なし → 参考として損失小さい順)"]
        with_data=[r for r in rows if r.get("profit_yen") is not None]
        for r in sorted(with_data, key=lambda x: x["profit_yen"], reverse=True)[:5]:
            mark=" 在庫処分" if r["is_clearance"] else ""
            lines.append(f"  {r['name'][:36]} 利益¥{r['profit_yen']:,} 仕入¥{r['net_cost']:,} vs メルカリ¥{r['mercari_median']:,} ({r['profit_margin_pct']}%){mark}")
    lines+=["","="*60]
    txt="\n".join(lines)
    summary_path=os.path.join(RESULTS_DIR, f"summer_handy_summary_{TODAY}.txt")
    with open(summary_path,"w",encoding="utf-8") as f: f.write(txt)
    print(txt)
    if profitable:
        print("\n"+"="*70)
        print(f"  利益あり商品（利益率順） 計{len(profitable)}件")
        print("="*70)
        for i,r in enumerate(sorted(profitable, key=lambda x: x["profit_margin_pct"], reverse=True),1):
            print(f"[{i:02d}] {r['name'][:36]}\n     仕入: ¥{r['net_cost']:,}  メルカリ: ¥{r['mercari_median']:,}  利益: ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%) 小型送料なら¥{r['profit_small_post']:,} {'[在庫処分]' if r['is_clearance'] else ''}\n     {r['yahoo_url']}\n")
    else:
        print("\n利益率10%以上は0件でした。ハンディファン参考詳細:")
        for r in handy:
            print(f"  {r['name'][:40]} | 仕入¥{r['net_cost']:,} -> メルカリ¥{r['mercari_median']} 利益¥{r['profit_yen']} ({r['profit_margin_pct']}%) 小型送料参考¥{r.get('profit_small_post')}")
    # 全体ハンディ一覧
    print("\n--- ハンディファン全件サマリー ---")
    for r in handy:
        print(f"{r['name'][:42]} | ¥{r['yahoo_price']:,} -> 実質¥{r['net_cost']:,} | メルカリ¥{r['mercari_median']} 利益¥{r['profit_yen']} | {r['yahoo_url']}")
    if driver: driver.quit()
    logger.info("=== 完了 ===")
    print(f"\nCSV: {csv_path}\nSummary: {summary_path}")

if __name__=="__main__":
    try: main()
    except Exception: logger.error(traceback.format_exc())

