"""
広域アービトラージ：家電全カテゴリ×6小売×GitHub統合
- GitHub: take-kun/mercapi + atushi1841/margin_analyzer + scikit-learn
- 対象をハンディファンから全カテゴリに拡大
- 価格帯も2000円→5000円以上の中高単価も含む
- クエリは汎用語15 + ブランド/型番シリーズ指定15の計30カテゴリ
  （汎用語だけだと年式やケーブル型番等のノイズを拾い赤字化するため、
  KJ-/NA-LX等のシリーズ名を付与したクエリを追加してSKU精度を上げる）
"""
import csv, os, re, asyncio
from datetime import datetime
from yahoo_mercari_arbitrage.mercapi_utils import fetch_mercapi_median
from yahoo_mercari_arbitrage.yahoo_utils import fetch_retailers_batched
from yahoo_mercari_arbitrage.playwright_utils import YahooAuctionFetcher

CONFIG={"YAHOO_APP_ID":"dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw","YAHOO_POINT_RATE":0.10,"COUPON_DISCOUNT":1000,"PAYPAY_RATE":0.01,"MIN_PROFIT_MARGIN":0.12,"MIN_COUNT":3,"MERCARI_FEE":0.10,"MIN_PRICE":5000}
SHIPPING_MAP={"テレビ":1500,"冷蔵庫":3000,"洗濯機":2500,"エアコン":3000,"パソコン":800,"カメラ":600,"扇風機本体":600,"その他":800}
def get_shipping(cat):
    for k,v in SHIPPING_MAP.items():
        if k in cat: return v
    return 800

RETAILERS={
    "ビックカメラ": ["y-sofmap","y-kojima"],
    "ヤマダデンキ": ["yamada-denki"],
    "エディオン": ["edion-tsutayakaden"],
    "ノジマ": ["yahoo_proxy"],
    "ヨドバシカメラ": ["yahoo_proxy"],
    "ケーズデンキ": ["yahoo_proxy"], # 追加：ケーズはYahooにないため代理
}
# 広域カテゴリ：夏物から通年・高単価へ拡大（汎用語15 + ブランド/型番シリーズ15）
BROAD_QUERIES=[
    "テレビ 4K","冷蔵庫","洗濯機 ドラム","エアコン 6畳","掃除機 コードレス",
    "電子レンジ","炊飯器","パソコン ノート","タブレット","カメラ ミラーレス",
    "ヘッドホン","ゲーム Switch","スマートウォッチ","美容家電 ドライヤー","調理家電",
    "SONY ブラビア KJ-","パナソニック 冷蔵庫 NR-F","パナソニック ドラム洗濯機 NA-LX",
    "ダイキン エアコン AN","ダイソン コードレス掃除機 V15",
    "シャープ ヘルシオ 電子レンジ","象印 炊飯器 NW-","Apple MacBook Air M",
    "iPad 第10世代","SONY α6400","SONY WH-1000XM5","Nintendo Switch 有機EL",
    "Apple Watch SE","ダイソン ドライヤー HD","バーミキュラ 炊飯器"
]
RESULTS_PER_QUERY=4
TODAY=datetime.now().strftime("%Y%m%d_%H%M")
RESULTS_DIR="results"; LOGS_DIR="logs"
import logging
os.makedirs(LOGS_DIR, exist_ok=True); os.makedirs(RESULTS_DIR, exist_ok=True)
logger=logging.getLogger("broad")
logger.setLevel(logging.DEBUG)
fmt=logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh=logging.FileHandler(os.path.join(LOGS_DIR, f"broad_{TODAY}.log"), encoding="utf-8")
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
ch=logging.StreamHandler(); ch.setLevel(logging.INFO); ch.setFormatter(fmt)
logger.handlers=[]; logger.addHandler(fh); logger.addHandler(ch)

def parse_hit(item, rank, retailer):
    price=item.get("price",0)
    point=item.get("point",{})
    times=(point.get("lyLimitedBonusTimes",0) or point.get("lyLimitedPremiumBonusTimes",0) or 0)
    rate=times/100.0 if times>1 else 0.0
    return {"rank":rank,"name":item.get("name",""),"price":price,"seller_name":item.get("seller",{}).get("name",retailer),"url":item.get("url",""),"point_rate":rate,"category":item.get("genreCategory",{}).get("name",""),"review_count":item.get("review",{}).get("count",0),"code":item.get("code",""),"retailer":retailer}

def calc_net(price, rate):
    after=max(price-CONFIG["COUPON_DISCOUNT"],0)
    net=after - after*rate - after*CONFIG["PAYPAY_RATE"]
    return {"net_cost":round(net)}

def extract_model(name):
    m=re.findall(r"[A-Z0-9\-]{3,}", name.upper())
    cands=[x for x in m if len(x)>=4 and x not in ["YAHOO","WHITE","BLACK","JAPAN"]]
    if cands:
        cands.sort(key=len, reverse=True)
        hyphen=[x for x in cands if "-" in x]
        if hyphen: return hyphen[0]
        return cands[0]
    return None

async def main():
    started=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"=== 広域6社×{len(BROAD_QUERIES)}カテゴリ GitHub統合版 開始 ===")
    hit_tuples=fetch_retailers_batched(CONFIG["YAHOO_APP_ID"], RETAILERS, BROAD_QUERIES, RESULTS_PER_QUERY, price_from=CONFIG["MIN_PRICE"])
    all_hits=[h for h,_,_ in hit_tuples]
    logger.info(f"総取得 {len(all_hits)}件")
    items=[]
    for i,(h,retailer,query) in enumerate(hit_tuples):
        parsed=parse_hit(h,i+1,retailer)
        parsed["retailer"]=retailer
        parsed["query"]=query
        if parsed["price"]>=CONFIG["MIN_PRICE"]:
            rate=parsed["point_rate"] or CONFIG["YAHOO_POINT_RATE"]
            if rate==0: rate=CONFIG["YAHOO_POINT_RATE"]
            parsed.update(calc_net(parsed["price"], rate))
            items.append(parsed)
    logger.info(f"価格フィルタ後 {len(items)}件")
    # カテゴリ分散を保ちつつ、多様な商品を30件に絞る（各小売・各カテゴリから均等に）
    # 単純にレビュー数上位30件ではなく、カテゴリごとに1件ずつピックして多様性確保
    from collections import defaultdict
    by_query=defaultdict(list)
    for it in items:
        by_query[it["query"]].append(it)
    target=[]
    # ラウンドロビンで各クエリから1件ずつ
    while len(target)<30 and any(by_query.values()):
        for q in BROAD_QUERIES:
            if by_query[q]:
                # そのクエリ内でレビュー数上位を1件
                by_query[q].sort(key=lambda x: x["review_count"], reverse=True)
                target.append(by_query[q].pop(0))
                if len(target)>=30: break
    logger.info(f"照合対象 {len(target)}件（カテゴリ分散）")
    auction=YahooAuctionFetcher(price_min=1000, price_max=1000000)
    auction_ready=await auction.start()
    rows=[]
    csv_path=os.path.join(RESULTS_DIR, f"broad_{TODAY}.csv")
    cols=["rank","retailer","query","name","model","category","yahoo_price","net_cost","mercapi_median","mercapi_count","yahooAuction_median","yahooAuction_count","conservative_median","shipping","profit_yen","profit_margin","is_profitable","confidence","total_count","yahoo_url"]
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=cols); w.writeheader()
    for it in target:
        model=extract_model(it["name"])
        logger.info(f"[{it['rank']:02d}] {it['retailer']}/{it['query'][:6]} {it['name'][:28]} model={model}")
        m_data=await fetch_mercapi_median(model)
        await asyncio.sleep(0.8)
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
        row={"rank":it["rank"],"retailer":it["retailer"],"query":it["query"],"name":it["name"],"model":model,"category":it["category"],"yahoo_price":it["price"],"net_cost":it["net_cost"],"mercapi_median":m_med,"mercapi_count":m_data["count"] if m_data else 0,"yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,"conservative_median":cons,"shipping":ship,"profit_yen":round(profit) if profit is not None else "","profit_margin":round(margin,2) if margin is not None else "","is_profitable":is_prof,"confidence":conf,"total_count":total,"yahoo_url":it["url"]}
        rows.append(row)
        with open(csv_path,"a",newline="",encoding="utf-8-sig") as f:
            w=csvm.DictWriter(f, fieldnames=cols); w.writerow(row)
        logger.info(f" -> cons{cons} profit{row['profit_yen']} conf{conf}")
    if auction_ready: await auction.stop()
    prof=[r for r in rows if r["is_profitable"]]
    from collections import Counter
    cnt_retailer=Counter([r["retailer"] for r in prof])
    cnt_query=Counter([r["query"] for r in prof])
    print(f"\n=== 広域 利益あり {len(prof)}/{len(rows)} ===")
    print("小売別:", dict(cnt_retailer))
    print("カテゴリ別:", dict(cnt_query))
    for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True)[:10]:
        print(f"{r['retailer']:8} {r['query']:10} | {r['name'][:30]:30} | {r['model']:12} | cons{r['conservative_median']} profit{r['profit_yen']} {r['profit_margin']}%")
    print(f"CSV {csv_path}")
    target_path=os.path.join(RESULTS_DIR, f"broad_target_{TODAY}.csv")
    with open(target_path,"w",newline="",encoding="utf-8-sig") as f:
        import csv as csvm
        w=csvm.DictWriter(f, fieldnames=["retailer","query","product_name","model","yahoo_price","net_cost","profit_yen","yahoo_url"])
        w.writeheader()
        for r in sorted(prof, key=lambda x: x["profit_yen"] if isinstance(x["profit_yen"], int) else -999, reverse=True):
            w.writerow({"retailer":r["retailer"],"query":r["query"],"product_name":r["name"],"model":r["model"],"yahoo_price":r["yahoo_price"],"net_cost":r["net_cost"],"profit_yen":r["profit_yen"],"yahoo_url":r["yahoo_url"]})
    print(f"対象リスト {target_path}")

if __name__=="__main__":
    asyncio.run(main())
