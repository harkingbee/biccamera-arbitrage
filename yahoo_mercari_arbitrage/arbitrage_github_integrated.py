"""
GitHub統合版：Yahoo Shopping API + take-kun/mercapi + Yahoo Auctionスクレイピング
- Yahoo: 直接API（yahoo-shoppingラッパー相当、APP_IDはCONFIGから）
- Mercari: GitHub take-kun/mercapi (APIレベル、Selenium不要)
- Yahoo Auction: microsoft/playwright + 軽量ステルス（旧SeleniumはWebDriverフラグ等で検出されやすいため移行）
保守的中央値 + 件数閾値 + カテゴリ別送料
"""
import csv, os, re, statistics, time, asyncio
from datetime import datetime
from urllib.parse import quote
import requests

# GitHub mercapi（共有ラッパー：ブロック対策のバックオフ+キャッシュ）
from yahoo_mercari_arbitrage.mercapi_utils import fetch_mercapi_median
from yahoo_mercari_arbitrage.playwright_utils import YahooAuctionFetcher

CONFIG={
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "YAHOO_POINT_RATE":0.10,
    "COUPON_DISCOUNT":1000,
    "PAYPAY_RATE":0.01,
    "MIN_PROFIT_MARGIN":0.10,
    "MIN_COUNT":3,
    "MERCARI_FEE":0.10,
    "MIN_PRICE":2000,
}
SHIPPING_MAP={"扇風機本体":600,"除湿機":1200,"家庭用エアコン":3000,"その他":600}
def get_shipping(cat):
    for k,v in SHIPPING_MAP.items():
        if k in cat: return v
    return 600

BIC_SELLERS=["y-sofmap","y-kojima"]
QUERIES=["ハンディファン","扇風機","ネッククーラー","サーキュレーター","除湿機","アウトレット"]
RESULTS_PER_QUERY=8
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"
LOGS_DIR="logs"
import logging
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("github_integrated")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"github_integrated_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
ch=logging.StreamHandler(); ch.setLevel(logging.INFO); ch.setFormatter(fmt)
logger.handlers=[]; logger.addHandler(fh); logger.addHandler(ch)

def fetch_yahoo_seller(seller_id, query, results=8):
    url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    params={"appid":CONFIG["YAHOO_APP_ID"],"query":query,"seller_id":seller_id,"price_from":CONFIG["MIN_PRICE"],"sort":"-review_count","in_stock":"true","results":results}
    for attempt in range(1,4):
        try:
            logger.info(f"Yahoo seller={seller_id} query={query}")
            r=requests.get(url, params=params, timeout=15)
            if r.status_code==429:
                w=10*attempt
                logger.warning(f"429 wait {w}s"); time.sleep(w); continue
            r.raise_for_status()
            data=r.json()
            hits=data.get("hits",[])
            logger.info(f" -> {len(hits)} total {data.get('totalResultsAvailable')}")
            return hits
        except Exception as e:
            logger.warning(f" err {e}")
            time.sleep(2**attempt)
    return []

def parse_hit(item, rank):
    price=item.get("price",0)
    point=item.get("point",{})
    times=(point.get("lyLimitedBonusTimes",0) or point.get("lyLimitedPremiumBonusTimes",0) or point.get("premiumBonusTimes",0))
    rate=times/100.0 if times>1 else 0.0
    name=item.get("name","")
    # 割引検出 priceLabel
    pl=item.get("priceLabel",{})
    default=pl.get("defaultPrice")
    discounted=pl.get("discountedPrice")
    has_discount=False; discount_rate=0
    if discounted and default and discounted < default:
        has_discount=True
        discount_rate=round((default-discounted)/default*100,1)
    return {"rank":rank,"name":name,"price":price,"seller_name":item.get("seller",{}).get("name",""),"url":item.get("url",""),"point_rate":rate,"category":item.get("genreCategory",{}).get("name",""),"review_count":item.get("review",{}).get("count",0),"code":item.get("code",""),"has_discount":has_discount,"discount_rate":discount_rate,"default_price":default}

def calc_net(price, rate):
    after=max(price-CONFIG["COUPON_DISCOUNT"],0)
    net=after - after*rate - after*CONFIG["PAYPAY_RATE"]
    return {"net_cost":round(net),"point_return":round(after*rate),"paypay_return":round(after*CONFIG["PAYPAY_RATE"])}

def extract_model(name):
    m=re.findall(r"[A-Z0-9\-]{3,}", name.upper())
    cands=[x for x in m if len(x)>=4 and x not in ["SIROCA","SONY","YAHOO","WHITE","BLACK"]]
    if cands:
        cands.sort(key=len, reverse=True)
        hyphen=[x for x in cands if "-" in x]
        if hyphen: return hyphen[0]
        return cands[0]
    return None

async def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== GitHub統合版 スキャン開始 ===")
    all_hits=[]; seen=set()
    for seller in BIC_SELLERS:
        for q in QUERIES:
            hits=fetch_yahoo_seller(seller,q,RESULTS_PER_QUERY)
            for h in hits:
                code=h.get("code","")
                if code in seen: continue
                seen.add(code); all_hits.append(h)
            time.sleep(2)
    logger.info(f"重複排除後 {len(all_hits)}件")
    items=[parse_hit(h,i+1) for i,h in enumerate(all_hits)]
    items=[it for it in items if it["price"]>=CONFIG["MIN_PRICE"]]
    for it in items:
        rate=it["point_rate"] or CONFIG["YAHOO_POINT_RATE"]
        if rate==0: rate=CONFIG["YAHOO_POINT_RATE"]
        it.update(calc_net(it["price"], rate))
    # ハンディ優先で35件
    def sort_key(x):
        return (1 if "ハンディ" in x["name"] else 0, x["review_count"])
    target=sorted(items, key=sort_key, reverse=True)[:30]
    logger.info(f"照合対象 {len(target)}件")
    auction=YahooAuctionFetcher()
    auction_ready=await auction.start()
    if auction_ready: logger.info("Playwright 起動 (Yahoo Auction用)")
    else: logger.warning("Playwright未起動、Yahoo Auction相場はスキップ")
    rows=[]
    csv_path=os.path.join(RESULTS_DIR, f"github_integrated_{TODAY}.csv")
    cols=["rank","name","model","category","seller","yahoo_price","net_cost","has_discount","discount_rate","mercapi_median","mercapi_count","mercapi_total_found","yahooAuction_median","yahooAuction_count","conservative_median","shipping","profit_yen","profit_margin","profit_small","is_profitable","confidence","total_count","yahoo_url"]
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=cols)
        w.writeheader()
    for it in target:
        model=extract_model(it["name"])
        logger.info(f"[{it['rank']:02d}] {it['name'][:32]} model={model} has_discount={it['has_discount']} {it['discount_rate']}%")
        # GitHub mercapi
        m_data=await fetch_mercapi_median(model)
        await asyncio.sleep(1.2)
        # Yahoo Auction
        if auction_ready and model and model!="SWITCHBOT":
            y_data=await auction.fetch(model)
        else:
            y_data={"median":None,"count":0}
        m_med=m_data["median"] if m_data else None
        y_med=y_data["median"] if y_data else None
        if m_med and y_med:
            cons=min(m_med,y_med)
        elif m_med:
            cons=m_med
        elif y_med:
            cons=y_med
        else:
            cons=None
        ship=get_shipping(it["category"])
        ship_small=300 if "ハンディ" in it["name"] else ship
        if cons:
            fee=cons*CONFIG["MERCARI_FEE"]
            profit=cons-fee-ship-it["net_cost"]
            margin=profit/cons*100 if cons else 0
            profit_small=cons-fee-ship_small-it["net_cost"]
            total=(m_data["count"] if m_data and m_data["count"] else 0)+(y_data["count"] if y_data["count"] else 0)
            is_prof= profit>0 and margin>=CONFIG["MIN_PROFIT_MARGIN"]*100 and total>=CONFIG["MIN_COUNT"]
            conf="high" if total>=5 else ("medium" if total>=3 else "low")
        else:
            profit=margin=profit_small=None; is_prof=False; conf="none"; total=0
        row={
            "rank":it["rank"],"name":it["name"],"model":model,"category":it["category"],"seller":it["seller_name"],
            "yahoo_price":it["price"],"net_cost":it["net_cost"],"has_discount":it["has_discount"],"discount_rate":it["discount_rate"],
            "mercapi_median":m_med,"mercapi_count":m_data["count"] if m_data else 0,"mercapi_total_found":m_data.get("total_found",0) if m_data else 0,
            "yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,
            "conservative_median":cons,"shipping":ship,
            "profit_yen":round(profit) if profit is not None else "",
            "profit_margin":round(margin,2) if margin is not None else "",
            "profit_small":round(profit_small) if profit_small is not None else "",
            "is_profitable":is_prof,"confidence":conf,"total_count":total,
            "yahoo_url":it["url"]
        }
        rows.append(row)
        with open(csv_path,"a",newline="",encoding="utf-8-sig") as f:
            w=csvm.DictWriter(f, fieldnames=cols)
            w.writerow(row)
        logger.info(f" -> cons{cons} m{m_med}({m_data['count'] if m_data else 0}) y{y_med}({y_data['count'] if y_data else 0}) profit{row['profit_yen']} conf{conf} discount{it['discount_rate']}%")
        await asyncio.sleep(0.3)
    if auction_ready: await auction.stop()
    # サマリー
    prof=[r for r in rows if r["is_profitable"]]
    lines=["="*60,"  GitHub統合版 ビックカメラ夏物スキャン","="*60,f"実施: {started}",f"対象: {len(rows)}件","GitHub: take-kun/mercapi + Yahoo Auction軽量","条件: 利益率10%+件数3件+保守的中央値",f"利益あり: {len(prof)}件","","【利益額TOP5】"]
    for i,r in enumerate(sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:5],1):
        lines.append(f"  {i}. {r['name'][:38]} ¥{r['profit_yen']:,} ({r['profit_margin']}%) {r['model']}  mercapi{r['mercapi_median']}x{r['mercapi_count']} yahoo{r['yahooAuction_median']}x{r['yahooAuction_count']}")
    if not prof:
        lines+=["  なし（参考：損失小さい順）"]
        for r in sorted([x for x in rows if isinstance(x["profit_yen"], int)], key=lambda x: x["profit_yen"], reverse=True)[:3]:
            lines.append(f"  {r['name'][:36]} 利益{r['profit_yen']} net{r['net_cost']} cons{r['conservative_median']}")
    lines+=["","="*60]
    txt="\n".join(lines)
    print(txt)
    summary_path=os.path.join(RESULTS_DIR, f"github_integrated_summary_{TODAY}.txt")
    with open(summary_path,"w",encoding="utf-8") as f: f.write(txt)
    logger.info(f"CSV {csv_path} Summary {summary_path}")
    # 詳細
    if prof:
        print("\n"+"="*70)
        for r in sorted(prof, key=lambda x: x["profit_margin"] if isinstance(x["profit_margin"], float) else -999, reverse=True):
            print(f"{r['name'][:36]} | model{r['model']} | net¥{r['net_cost']:,} cons¥{r['conservative_median']:,} profit¥{r['profit_yen']:,} {r['profit_margin']}% cnt{r['total_count']} {r['confidence']} disc{r['discount_rate']}% | {r['yahoo_url']}")

if __name__=="__main__":
    asyncio.run(main())
