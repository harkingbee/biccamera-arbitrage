"""
スマートフォン関連広域：6小売×スマホ8クエリ×GitHub統合
"""
import csv, os, re, statistics, time, asyncio
from datetime import datetime
from urllib.parse import quote
import requests
from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    SELENIUM_AVAILABLE=True
except: SELENIUM_AVAILABLE=False

CONFIG={"YAHOO_APP_ID":"dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw","YAHOO_POINT_RATE":0.10,"COUPON_DISCOUNT":1000,"PAYPAY_RATE":0.01,"MIN_PROFIT_MARGIN":0.08,"MIN_COUNT":3,"MERCARI_FEE":0.10,"MIN_PRICE":8000}
SHIPPING_MAP={"スマートフォン":600,"ケース":300,"フィルム":300,"バッテリー":500,"その他":600}
def get_shipping(cat):
    for k,v in SHIPPING_MAP.items():
        if k in cat: return v
    return 600

RETAILERS={
    "ビックカメラ": ["y-sofmap","y-kojima"],
    "ヤマダデンキ": ["yamada-denki"],
    "エディオン": ["edion-tsutayakaden"],
    "ノジマ": ["yahoo_proxy"],
    "ヨドバシカメラ": ["yahoo_proxy"],
    "ケーズデンキ": ["yahoo_proxy"],
}
SMARTPHONE_QUERIES=[
    "iPhone 15","iPhone 14 中古","Xperia 1 V","Pixel 8","Galaxy S24",
    "中古スマホ","スマホケース iPhone","モバイルバッテリー 20000mAh"
]
RESULTS_PER_QUERY=5
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"; LOGS_DIR="logs"
import logging
os.makedirs(LOGS_DIR, exist_ok=True); os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("smartphone")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"smartphone_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
ch=logging.StreamHandler(); ch.setLevel(logging.INFO); ch.setFormatter(fmt)
logger.handlers=[]; logger.addHandler(fh); logger.addHandler(ch)

def fetch_yahoo_seller(seller_id, query, results=5):
    if seller_id=="yahoo_proxy":
        url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
        params={"appid":CONFIG["YAHOO_APP_ID"],"query":query,"results":results,"price_from":CONFIG["MIN_PRICE"],"sort":"-review_count"}
    else:
        url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
        params={"appid":CONFIG["YAHOO_APP_ID"],"query":query,"seller_id":seller_id,"results":results,"price_from":CONFIG["MIN_PRICE"],"sort":"-review_count","in_stock":"true"}
    for attempt in range(1,4):
        try:
            r=requests.get(url, params=params, timeout=15)
            if r.status_code==429:
                time.sleep(10*attempt); continue
            r.raise_for_status()
            hits=r.json().get("hits",[])
            logger.info(f" seller={seller_id} query={query} -> {len(hits)}")
            return hits
        except Exception as e:
            logger.warning(f" err {e}")
            time.sleep(2**attempt)
    return []

def parse_hit(item, rank, retailer):
    price=item.get("price",0)
    point=item.get("point",{})
    times=(point.get("lyLimitedBonusTimes",0) or point.get("lyLimitedPremiumBonusTimes",0) or 0)
    rate=times/100.0 if times>1 else 0.0
    return {"rank":rank,"name":item.get("name",""),"price":price,"seller_name":item.get("seller",{}).get("name",retailer),"url":item.get("url",""),"point_rate":rate,"category":item.get("genreCategory",{}).get("name",""),"review_count":item.get("review",{}).get("count",0),"code":item.get("code",""),"retailer":retailer,"query":item.get("_query","")}

def calc_net(price, rate):
    after=max(price-CONFIG["COUPON_DISCOUNT"],0)
    net=after - after*rate - after*CONFIG["PAYPAY_RATE"]
    return {"net_cost":round(net)}

def extract_model(name):
    # スマホは型番が複雑：iPhone15, SM-S928, Pixel8 など
    m=re.findall(r"(IPHONE\s*\d+|PIXEL\s*\d+|XPERIA\s*[\w\-]+|GALAXY\s*S\d+|SM-[A-Z0-9]+|SCG\d+|SO-\d+)", name.upper())
    if m:
        return re.sub(r"\s+","",m[0])
    # フォールバック：英数字ハイフン
    m2=re.findall(r"[A-Z0-9\-]{4,}", name.upper())
    cands=[x for x in m2 if len(x)>=5 and x not in ["YAHOO","IPHONE","GALAXY"]]
    if cands:
        cands.sort(key=len, reverse=True)
        return cands[0]
    return None

def build_driver():
    if not SELENIUM_AVAILABLE: return None
    opts=Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches",["enable-automation"])
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    except:
        d=webdriver.Chrome(options=opts)
    d.implicitly_wait(3)
    return d

def fetch_yahooAuction(model, driver):
    if not model: return {"median":None,"count":0}
    from urllib.parse import quote as qq
    url=f"https://auctions.yahoo.co.jp/search/search?p={qq(model)}&va={qq(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url); time.sleep(2)
    for sel in ["span[class*='Price']","span[class*='price']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:20]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 3000 <= int(txt) <= 300000:
                    prices.append(int(txt))
            if prices:
                import statistics
                return {"median":round(statistics.median(prices)),"count":len(prices)}
    return {"median":None,"count":0}

async def fetch_mercapi(model):
    if not model: return {"median":None,"count":0}
    m=Mercapi()
    try:
        res=await m.search(model, status=[SearchRequestData.Status.STATUS_SOLD_OUT])
        if not res.items: return {"median":None,"count":0}
        import statistics
        prices=[x.price for x in res.items[:20]]
        return {"median":round(statistics.median(prices)),"count":len(res.items)}
    except: return {"median":None,"count":0}

async def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== スマホ関連 6社×8クエリ 開始 ===")
    all_hits=[]
    for retailer, sellers in RETAILERS.items():
        for seller in sellers:
            for q in SMARTPHONE_QUERIES:
                hits=fetch_yahoo_seller(seller, q, RESULTS_PER_QUERY)
                for h in hits:
                    h["_retailer"]=retailer
                    h["_query"]=q
                    all_hits.append(h)
                time.sleep(1.2)
    logger.info(f"総取得 {len(all_hits)}件")
    items=[]
    for i,h in enumerate(all_hits):
        retailer=h.pop("_retailer","unknown")
        query=h.pop("_query","")
        parsed=parse_hit(h,i+1,retailer)
        parsed["retailer"]=retailer
        parsed["query"]=query
        if parsed["price"]>=CONFIG["MIN_PRICE"]:
            rate=parsed["point_rate"] or CONFIG["YAHOO_POINT_RATE"]
            if rate==0: rate=CONFIG["YAHOO_POINT_RATE"]
            parsed.update(calc_net(parsed["price"], rate))
            items.append(parsed)
    logger.info(f"価格フィルタ後 {len(items)}件")
    # スマホは高単価なのでレビュー数より価格帯で分散
    target=sorted(items, key=lambda x: x["review_count"], reverse=True)[:30]
    logger.info(f"照合対象 {len(target)}件")
    driver=build_driver()
    rows=[]
    csv_path=os.path.join(RESULTS_DIR, f"smartphone_{TODAY}.csv")
    cols=["rank","retailer","query","name","model","category","yahoo_price","net_cost","mercapi_median","mercapi_count","yahooAuction_median","yahooAuction_count","conservative_median","shipping","profit_yen","profit_margin","is_profitable","confidence","total_count","yahoo_url"]
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=cols); w.writeheader()
    for it in target:
        model=extract_model(it["name"])
        logger.info(f"[{it['rank']:02d}] {it['retailer']}/{it['query']} {it['name'][:30]} model={model}")
        m_data=await fetch_mercapi(model)
        await asyncio.sleep(0.8)
        y_data=fetch_yahooAuction(model, driver) if driver else {"median":None,"count":0}
        time.sleep(1)
        m_med=m_data["median"] if m_data else None
        y_med=y_data["median"] if y_data else None
        if m_med and y_med: cons=min(m_med,y_med)
        elif m_med: cons=m_med
        elif y_med: cons=y_med
        else: cons=None
        ship=get_shipping(it["category"])
        if cons:
            fee=cons*CONFIG["MERCARI_FEE"]
            profit=cons-fee-ship-it["net_cost"]
            margin=profit/cons*100 if cons else 0
            total=(m_data["count"] if m_data else 0)+(y_data["count"] if y_data else 0)
            is_prof=profit>0 and margin>=CONFIG["MIN_PROFIT_MARGIN"]*100 and total>=CONFIG["MIN_COUNT"]
            conf="high" if total>=5 else ("medium" if total>=3 else "low")
        else:
            profit=margin=None; is_prof=False; conf="none"; total=0
        row={"rank":it["rank"],"retailer":it["retailer"],"query":it["query"],"name":it["name"],"model":model,"category":it["category"],"yahoo_price":it["price"],"net_cost":it["net_cost"],"mercapi_median":m_med,"mercapi_count":m_data["count"] if m_data else 0,"yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,"conservative_median":cons,"shipping":ship,"profit_yen":round(profit) if profit is not None else "","profit_margin":round(margin,2) if margin is not None else "","is_profitable":is_prof,"confidence":conf,"total_count":total,"yahoo_url":it["url"]}
        rows.append(row)
        with open(csv_path,"a",newline="",encoding="utf-8-sig") as f:
            w=csvm.DictWriter(f, fieldnames=cols); w.writerow(row)
        logger.info(f" -> cons{cons} profit{row['profit_yen']} conf{conf}")
    if driver: driver.quit()
    prof=[r for r in rows if r["is_profitable"]]
    from collections import Counter
    print(f"\n=== スマホ関連 利益あり {len(prof)}/{len(rows)} ===")
    print("小売別:", dict(Counter([r["retailer"] for r in prof])))
    print("クエリ別:", dict(Counter([r["query"] for r in prof])))
    for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:10]:
        print(f"{r['retailer']:8} {r['query']:15} | {r['name'][:32]:32} | {r['model']:12} | cons{r['conservative_median']} profit{r['profit_yen']} {r['profit_margin']}%")
    print(f"CSV {csv_path}")
    target_path=os.path.join(RESULTS_DIR, f"smartphone_target_{TODAY}.csv")
    with open(target_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=["retailer","query","product_name","model","yahoo_price","net_cost","profit_yen","yahoo_url"])
        w.writeheader()
        for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True):
            w.writerow({"retailer":r["retailer"],"query":r["query"],"product_name":r["name"],"model":r["model"],"yahoo_price":r["yahoo_price"],"net_cost":r["net_cost"],"profit_yen":r["profit_yen"],"yahoo_url":r["yahoo_url"]})
    print(f"対象リスト {target_path}")

if __name__=="__main__":
    asyncio.run(main())
