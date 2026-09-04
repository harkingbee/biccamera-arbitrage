"""
ヨドバシ.com対応アービトラージ（ノジマ/ビックカメラと同ロジック）
- GitHub統合版と同じく mercapi + Yahoo Auctionで保守的中央値
- ヨドバシ.comはPlaywright(Chromium)からの直接アクセスはHTTP2レベルで拒否されるが、
  curl_cffi（TLSフィンガープリントを実際のChromeに偽装）なら安定して取得できることを
  確認済み（有料プロキシ不要。ノジマと異なりブロックはほぼ発生しない）
  1. curl_cffiによる直接取得（direct_retailer_utils.fetch_yodobashi、リトライ内蔵）
  2. フォールバック：Yahooショッピングの汎用検索結果をヨドバシ想定で代替
"""
import csv, os, re, time, asyncio
from datetime import datetime
import requests
from yahoo_mercari_arbitrage.mercapi_utils import fetch_mercapi_median
from yahoo_mercari_arbitrage.playwright_utils import YahooAuctionFetcher
from yahoo_mercari_arbitrage.direct_retailer_utils import fetch_yodobashi

CONFIG={
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE":0.10, # ヨドバシはゴールドポイント基本10%還元
    "COUPON_DISCOUNT":0,     # ヨドバシは基本クーポン無し
    "PAYPAY_RATE":0.0,       # ヨドバシはPayPay非対応（自社ポイントのみ）
    "MIN_PROFIT_MARGIN":0.10,
    "MIN_COUNT":3,
    "MERCARI_FEE":0.10,
    "MIN_PRICE":2000,
}
SHIPPING_MAP={"扇風機本体":600,"除湿機":1200,"家庭用エアコン":2500,"その他":600}
def get_shipping(cat):
    for k,v in SHIPPING_MAP.items():
        if k in cat: return v
    return 600

YODOBASHI_QUERIES=["ハンディファン","扇風機","ネッククーラー","サーキュレーター","冷風機"]
RESULTS_PER_QUERY=8
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"
LOGS_DIR="logs"
import logging
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("yodobashi")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"yodobashi_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
ch=logging.StreamHandler(); ch.setLevel(logging.INFO); ch.setFormatter(fmt)
logger.handlers=[]; logger.addHandler(fh); logger.addHandler(ch)

def extract_model(name):
    m=re.findall(r"[A-Z0-9\-]{3,}", name.upper())
    cands=[x for x in m if len(x)>=4 and x not in ["YODOBASHI","SONY","YAHOO"]]
    if cands:
        cands.sort(key=len, reverse=True)
        hyphen=[x for x in cands if "-" in x]
        if hyphen: return hyphen[0]
        return cands[0]
    return None

# ---------- ヨドバシ.com取得 2段階 ----------

def fetch_yodobashi_via_yahoo_proxy(query, results=8):
    """フォールバック：直接取得が全リトライ失敗した場合のみ、Yahooショッピングの
    汎用検索結果をヨドバシ想定価格として代替利用する
    """
    url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    params={"appid":CONFIG["YAHOO_APP_ID"],"query":query,"results":results,"price_from":CONFIG["MIN_PRICE"],"sort":"-review_count"}
    try:
        r=requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        hits=r.json().get("hits",[])
        logger.info(f" Yahooプロキシで {query} -> {len(hits)}件（ヨドバシ想定補正）")
        return [
            {
                "name": h.get("name",""),
                "price": h.get("price",0),
                "url": h.get("url",""),
                "code": h.get("code",""),
                "seller_name": h.get("seller",{}).get("name","")+"(Yahoo代理)",
                "category": h.get("genreCategory",{}).get("name",""),
                "point_rate": 0.10,
            }
            for h in hits
        ]
    except Exception as e:
        logger.warning(f" Yahooプロキシ失敗 {e}")
        return []

def fetch_yodobashi_hybrid(query, results=8):
    """1. curl_cffiによる直接取得 → 2. Yahooプロキシへフォールバック"""
    hits=fetch_yodobashi(query, results)
    if hits:
        return hits
    logger.info(f" 直接取得失敗のためYahooプロキシへフォールバック: {query}")
    return fetch_yodobashi_via_yahoo_proxy(query, results)

def parse_yodobashi_hit(item, rank):
    return {
        "rank": rank,
        "name": item.get("name",""),
        "price": item.get("price",0),
        "seller_name": item.get("seller_name","ヨドバシ.com"),
        "url": item.get("url",""),
        "category": item.get("category",""),
        "point_rate": item.get("point_rate") or 0.10,
    }

def calc_net(price, rate):
    after=max(price-CONFIG["COUPON_DISCOUNT"],0)
    net=after - after*rate - after*CONFIG["PAYPAY_RATE"]
    return {"net_cost":round(net),"point_return":round(after*rate)}

async def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== ヨドバシ.com GitHub統合版 開始 ===")
    logger.info(" Yahoo→Mercari/YahooAuctionの保守的中央値で判定（ビックカメラと同ロジック）")
    all_hits=[]; seen=set()
    for q in YODOBASHI_QUERIES:
        hits=fetch_yodobashi_hybrid(q, RESULTS_PER_QUERY)
        for h in hits:
            code=h.get("code") or h.get("url","")
            if code in seen: continue
            seen.add(code); all_hits.append(h)
        time.sleep(2)
    logger.info(f"取得 {len(all_hits)}件")
    items=[parse_yodobashi_hit(h,i+1) for i,h in enumerate(all_hits)]
    items=[it for it in items if it["price"]>=CONFIG["MIN_PRICE"]]
    for it in items:
        rate=it["point_rate"] or CONFIG["YAHOO_POINT_RATE"]
        it.update(calc_net(it["price"], rate))
    # ハンディ優先で30件
    target=sorted(items, key=lambda x: (1 if "ハンディ" in x["name"] else 0, x.get("review_count",0)), reverse=True)[:30]
    logger.info(f"照合対象 {len(target)}件")
    auction=YahooAuctionFetcher()
    auction_ready=await auction.start()
    rows=[]
    csv_path=os.path.join(RESULTS_DIR, f"yodobashi_github_{TODAY}.csv")
    cols=["rank","name","model","category","seller","yahoo_price","net_cost","mercapi_median","mercapi_count","yahooAuction_median","yahooAuction_count","conservative_median","shipping","profit_yen","profit_margin","is_profitable","confidence","total_count","yahoo_url"]
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=cols); w.writeheader()
    for it in target:
        model=extract_model(it["name"])
        logger.info(f"[{it['rank']:02d}] {it['name'][:32]} model={model}")
        m_data=await fetch_mercapi_median(model)
        await asyncio.sleep(1)
        y_data=await auction.fetch(model) if auction_ready else {"median":None,"count":0}
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
        row={"rank":it["rank"],"name":it["name"],"model":model,"category":it["category"],"seller":it["seller_name"],"yahoo_price":it["price"],"net_cost":it["net_cost"],"mercapi_median":m_med,"mercapi_count":m_data["count"] if m_data else 0,"yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,"conservative_median":cons,"shipping":ship,"profit_yen":round(profit) if profit is not None else "","profit_margin":round(margin,2) if margin is not None else "","is_profitable":is_prof,"confidence":conf,"total_count":total,"yahoo_url":it["url"]}
        rows.append(row)
        with open(csv_path,"a",newline="",encoding="utf-8-sig") as f:
            w=csvm.DictWriter(f, fieldnames=cols); w.writerow(row)
        logger.info(f" -> cons{cons} profit{row['profit_yen']} conf{conf}")
    if auction_ready: await auction.stop()
    prof=[r for r in rows if r["is_profitable"]]
    print(f"\n=== ヨドバシ.com GitHub統合版 利益あり {len(prof)}/{len(rows)} ===")
    for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:5]:
        print(f"{r['name'][:38]} | {r['model']} | cons{r['conservative_median']} profit{r['profit_yen']} {r['profit_margin']}%")
    print(f"CSV {csv_path}")
    target_path=os.path.join(RESULTS_DIR, f"yodobashi_target_{TODAY}.csv")
    with open(target_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=["rank","product_name","model","yahoo_price","net_cost","profit_yen","yahoo_url"])
        w.writeheader()
        for i,r in enumerate(sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True),1):
            w.writerow({"rank":i,"product_name":r["name"],"model":r["model"],"yahoo_price":r["yahoo_price"],"net_cost":r["net_cost"],"profit_yen":r["profit_yen"],"yahoo_url":r["yahoo_url"]})
    print(f"対象リスト {target_path}")

if __name__=="__main__":
    asyncio.run(main())
