"""
夏物在庫処分フル再スキャン：型番のみ検索 + メルカリ×ヤフオク保守的中央値
"""
import csv, os, re, statistics, time, traceback
from datetime import datetime
from urllib.parse import quote
import requests

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    SELENIUM_AVAILABLE=True
except ImportError:
    SELENIUM_AVAILABLE=False

CONFIG={
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE": 0.10,
    "COUPON_DISCOUNT": 1000,
    "PAYPAY_RATE": 0.01,
    "MIN_PROFIT_MARGIN": 0.10,
    "MERCARI_FEE": 0.10,
    "SHIPPING": 600,
    "SHIPPING_SMALL": 300,
    "MIN_PRICE": 2000,
    "API_RETRY_MAX": 3,
}

BIC_SELLERS=["y-sofmap","y-kojima"]
QUERIES=["ハンディファン","扇風機","ネッククーラー","サーキュレーター","除湿機","アウトレット"]
RESULTS_PER_QUERY=12
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"
LOGS_DIR="logs"

import logging
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("fulldual")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"fulldual_{TODAY}.log"), encoding="utf-8")
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
            logger.info(f" -> {len(hits)} total {data.get('totalResultsAvailable')}")
            return hits
        except Exception as e:
            logger.warning(f" err {e}")
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
    name=item.get("name","")
    clearance_keywords=["訳あり","アウトレット","処分","在庫限り","箱不良","外箱不良","展示品"]
    is_clearance=any(k in name for k in clearance_keywords)
    return {"rank":rank,"name":name,"price":price,"seller_name":item.get("seller",{}).get("name",""),"url":item.get("url",""),"point_rate":rate,"category":item.get("genreCategory",{}).get("name",""),"review_count":item.get("review",{}).get("count",0),"code":item.get("code",""),"is_clearance":is_clearance}

def calc_net(price, rate):
    after=max(price-CONFIG["COUPON_DISCOUNT"],0)
    net=after - after*rate - after*CONFIG["PAYPAY_RATE"]
    return {"price_after":after,"point_return":round(after*rate),"paypay_return":round(after*CONFIG["PAYPAY_RATE"]),"net_cost":round(net)}

def extract_model(name):
    m=re.findall(r"[A-Z0-9\-]{3,}", name.upper())
    cands=[x for x in m if len(x)>=4 and x not in ["SIROCA","SONY","YAHOO","WHITE","BLACK","BEIGE","GRAY"]]
    if cands:
        cands.sort(key=len, reverse=True)
        # 型番らしいハイフン含むものを優先
        hyphen=[x for x in cands if "-" in x]
        if hyphen:
            return hyphen[0]
        return cands[0]
    return None

def build_driver():
    opts=Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--window-size=1280,900")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    except Exception:
        d=webdriver.Chrome(options=opts)
    d.implicitly_wait(4)
    return d

def fetch_mercari(model, driver, n=12):
    if not model:
        return None
    url=f"https://www.mercari.com/jp/search/?keyword={quote(model)}&status=sold_out"
    driver.get(url)
    try:
        WebDriverWait(driver,4).until(EC.presence_of_element_located((By.CSS_SELECTOR,"span.merPrice, [class*='priceContainer']")))
    except:
        time.sleep(1.0)
    for sel in ["span.merPrice","span[class*='priceContainer']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:n]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 500 <= int(txt) <= 300000:
                    prices.append(int(txt))
            if prices:
                return {"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"count":len(prices),"prices":prices}
    return None

def fetch_yahooAuction(model, driver, n=12):
    if not model:
        return None
    url=f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url)
    time.sleep(1.0)
    for sel in ["span[class*='Price__value']","span[class*='Price']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:n]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 500 <= int(txt) <= 500000:
                    prices.append(int(txt))
            if prices:
                return {"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"count":len(prices),"prices":prices}
    return None

def calc_profit(net, median):
    if median is None:
        return None
    fee=median*CONFIG["MERCARI_FEE"]
    profit=median-fee-CONFIG["SHIPPING"]-net
    margin=profit/median*100 if median else 0
    profit_small=median-fee-CONFIG["SHIPPING_SMALL"]-net
    return {"profit":round(profit),"margin":round(margin,2),"profit_small":round(profit_small),"is_profitable": profit>0 and margin>=CONFIG["MIN_PROFIT_MARGIN"]*100}

def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== フル再スキャン開始（型番のみ＋ヤフオク併用）===")
    all_hits=[]; seen=set()
    for seller in BIC_SELLERS:
        for q in QUERIES:
            hits=fetch_for_seller(seller,q,RESULTS_PER_QUERY)
            for h in hits:
                code=h.get("code","")
                if code in seen: continue
                seen.add(code); all_hits.append(h)
            time.sleep(3)
    logger.info(f"重複排除後 {len(all_hits)}件")
    items=[parse_hit(h,i+1) for i,h in enumerate(all_hits)]
    items=[it for it in items if it["price"]>=CONFIG["MIN_PRICE"]]
    for it in items:
        rate=it["point_rate"] or CONFIG["YAHOO_POINT_RATE"]
        if rate==0: rate=CONFIG["YAHOO_POINT_RATE"]
        it.update(calc_net(it["price"], rate))
    # ハンディ優先ソート
    def sort_key(x):
        is_handy=1 if "ハンディ" in x["name"] else 0
        return (is_handy, x["review_count"])
    items_sorted=sorted(items, key=sort_key, reverse=True)
    target=items_sorted[:35]
    logger.info(f"照合対象 {len(target)}件")
    driver=build_driver()
    logger.info("Selenium 起動")
    rows=[]
    # 逐次書き込み用にヘッダー準備
    csv_path=os.path.join(RESULTS_DIR, f"summer_full_dual_{TODAY}.csv")
    cols=["rank","name","category","seller","model","yahoo_price","net_cost","is_clearance","mercari_median","mercari_count","yahooAuction_median","yahooAuction_count","conservative_median","avg_median","profit_yen","profit_margin_pct","profit_small_post","is_profitable","yahoo_url"]
    # 初期書き込み
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
    for it in target:
        model=extract_model(it["name"])
        kw=model if model else it["name"][:12]
        logger.info(f"[{it['rank']:02d}] {it['name'][:32]} model={model} kw={kw}")
        m_data=fetch_mercari(kw, driver)
        time.sleep(1.0)
        y_data=fetch_yahooAuction(kw, driver)
        time.sleep(1.0)
        m_med=m_data["median"] if m_data else None
        y_med=y_data["median"] if y_data else None
        if m_med and y_med:
            conservative=min(m_med, y_med)
            avg=round((m_med+y_med)/2)
        elif m_med:
            conservative=m_med; avg=m_med
        elif y_med:
            conservative=y_med; avg=y_med
        else:
            conservative=None; avg=None
        profit_info=calc_profit(it["net_cost"], conservative) if conservative else None
        row={
            "rank":it["rank"],"name":it["name"],"category":it["category"],"seller":it["seller_name"],
            "model":model,"yahoo_price":it["price"],"net_cost":it["net_cost"],"is_clearance":it["is_clearance"],
            "mercari_median":m_med,"mercari_count":m_data["count"] if m_data else 0,
            "yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,
            "conservative_median":conservative,"avg_median":avg,
            "profit_yen":profit_info["profit"] if profit_info else None,
            "profit_margin_pct":profit_info["margin"] if profit_info else None,
            "profit_small_post":profit_info["profit_small"] if profit_info else None,
            "is_profitable":profit_info["is_profitable"] if profit_info else False,
            "yahoo_url":it["url"]
        }
        rows.append(row)
        # 逐次追記（タイムアウトで失われないように）
        with open(csv_path,"a",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f, fieldnames=cols)
            w.writerow(row)
        logger.info(f"  -> 追記保存 {row['profit_yen']}")
    driver.quit()
    logger.info(f"CSV {csv_path}")
    profitable=[r for r in rows if r["is_profitable"]]
    positive=[r for r in rows if r["profit_yen"] and r["profit_yen"]>0]
    handy=[r for r in rows if "ハンディ" in r["name"]]
    handy_prof=[r for r in handy if r["is_profitable"]]
    lines=["="*60,"  夏物フル再スキャン（型番のみ＋ヤフオク保守的）","="*60,f"実施日時: {started}",f"調査対象: {len(rows)}件 (ハンディファン {len(handy)}件)",f"利益あり(10%以上): {len(profitable)}件",f"利益プラス: {len(positive)}件",f"ハンディ内利益あり: {len(handy_prof)}件","","【利益額TOP5】"]
    for i,r in enumerate(sorted(profitable, key=lambda x: x["profit_yen"] or -999, reverse=True)[:5],1):
        lines.append(f"  {i}. {r['name'][:38]} ¥{r['profit_yen']:,} ({r['profit_margin_pct']}%)  mercari{r['mercari_median']} yahoo{r['yahooAuction_median']}->{r['conservative_median']}")
    lines+=["","【利益率TOP5】"]
    for i,r in enumerate(sorted(profitable, key=lambda x: x["profit_margin_pct"] or -999, reverse=True)[:5],1):
        lines.append(f"  {i}. {r['name'][:38]} {r['profit_margin_pct']}% (¥{r['profit_yen']:,})")
    if not profitable:
        lines+=["","参考：損失小さい順"]
        for r in sorted([x for x in rows if x["profit_yen"] is not None], key=lambda x: x["profit_yen"], reverse=True)[:5]:
            lines.append(f"  {r['name'][:36]} 利益{r['profit_yen']} net{r['net_cost']} cons{r['conservative_median']}")
    lines+=["","="*60]
    txt="\n".join(lines)
    summary_path=os.path.join(RESULTS_DIR, f"summer_full_dual_summary_{TODAY}.txt")
    with open(summary_path,"w",encoding="utf-8") as f: f.write(txt)
    print(txt)
    if profitable:
        print("\n"+"="*70)
        print(f"  利益あり（利益率順） {len(profitable)}件")
        print("="*70)
        for i,r in enumerate(sorted(profitable, key=lambda x: x["profit_margin_pct"] or -999, reverse=True),1):
            print(f"[{i:02d}] {r['name'][:36]}\n     model {r['model']} 仕入¥{r['net_cost']:,} 保守的¥{r['conservative_median']:,} (mercari{r['mercari_median']}x{r['mercari_count']} / yahoo{r['yahooAuction_median']}x{r['yahooAuction_count']}) 利益¥{r['profit_yen']:,} ({r['profit_margin_pct']}%) 小型¥{r['profit_small_post']}\n     {r['yahoo_url']}\n")
    else:
        print("利益ありなし")
    print(f"\nCSV: {csv_path}\nSummary: {summary_path}")
    # ハンディ全件も出力
    print("\n--- ハンディファン全件（新判定） ---")
    for r in handy:
        print(f"{r['name'][:42]} | ¥{r['yahoo_price']:,}→¥{r['net_cost']:,} | 保守¥{r['conservative_median']} 利益¥{r['profit_yen']} {r['profit_margin_pct']}% | {r['yahoo_url']}")

if __name__=="__main__":
    try: main()
    except Exception: logger.error(traceback.format_exc())

