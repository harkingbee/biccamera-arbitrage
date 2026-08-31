"""
v2 改善版：タイトルフィルタ＋件数閾値＋カテゴリ別送料＋割引率表示
既存の summer_full_dual_20260830_1234.csv をベースに再検証
"""
import csv, re, statistics, time
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import requests

CONFIG={
    "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw",
    "MERCARI_FEE":0.10,
    "MIN_MARGIN":10,
    "MIN_COUNT":3,  # 信頼できる最低取引件数
}
# カテゴリ別送料（ハンディファンは小型、サーキュレーターは中型）
SHIPPING_MAP={
    "扇風機本体":600, # ハンディは本来300だが保守的に600、smallは別途
    "ハンディークリーナー":700,
    "除湿機":1200,
    "家庭用エアコン":3000,
    "その他":600,
}
def get_shipping(category):
    for k,v in SHIPPING_MAP.items():
        if k in category:
            return v
    return 600

def build_driver():
    opts=Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--window-size=1280,900")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    d.implicitly_wait(4)
    return d

def fetch_mercari_filtered(model, driver):
    if not model:
        return {"median":None,"count":0,"prices":[],"filtered":0,"raw":0}
    url=f"https://www.mercari.com/jp/search/?keyword={quote(model)}&status=sold_out"
    driver.get(url)
    try:
        WebDriverWait(driver,3).until(EC.presence_of_element_located((By.CSS_SELECTOR,"span.merPrice")))
    except:
        time.sleep(1.0)
    prices_els=driver.find_elements(By.CSS_SELECTOR,"span.merPrice, span[class*='priceContainer']")
    # タイトル取得を複数セレクタで試す
    titles_els=[]
    for sel in ["[data-testid='itemName']","div[class*='ItemName']","p[class*='Title']","a div[class*='title']"]:
        titles_els=driver.find_elements(By.CSS_SELECTOR, sel)
        if titles_els:
            break
    prices_raw=[]
    for el in prices_els[:20]:
        txt=re.sub(r"[^\d]","",el.text)
        if txt and 500 <= int(txt) <= 500000:
            prices_raw.append(int(txt))
    if not prices_raw:
        return {"median":None,"count":0,"prices":[],"filtered":0,"raw":0}
    # タイトルフィルタ：タイトル数と価格数が一致すれば1対1でフィルタ、一致しなければ価格のみで判定（信頼度低）
    if titles_els and len(titles_els)==len(prices_els):
        filtered=[]
        for price, title_el in zip(prices_raw, [t.text for t in titles_els[:len(prices_raw)]]):
            if model.upper() in title_el.upper():
                filtered.append(price)
        if filtered:
            return {"median":round(statistics.median(filtered)),"mean":round(statistics.mean(filtered)),"count":len(filtered),"prices":filtered,"filtered":len(filtered),"raw":len(prices_raw)}
        else:
            # タイトルに型番を含むものが0件 → 型番不一致のため取引なしとみなす
            return {"median":None,"count":0,"prices":[],"filtered":0,"raw":len(prices_raw)}
    else:
        # タイトル取得不可 → 価格のみで判定だが信頼度注記
        return {"median":round(statistics.median(prices_raw)),"mean":round(statistics.mean(prices_raw)),"count":len(prices_raw),"prices":prices_raw,"filtered":len(prices_raw),"raw":len(prices_raw),"note":"title_unavailable"}

def fetch_yahooAuction_filtered(model, driver):
    if not model or model=="SWITCHBOT":
        return {"median":None,"count":0}
    url=f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url)
    time.sleep(1.5)
    # 価格取得
    prices=[]
    for sel in ["span[class*='Price']","span[class*='price']","p[class*='price']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            for el in els[:20]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 500 <= int(txt) <= 500000:
                    prices.append(int(txt))
            break
    if not prices:
        return {"median":None,"count":0}
    # タイトル取得
    titles_els=[]
    for sel in ["a.Product__title","h3.Product__title","a[class*='title']"]:
        titles_els=driver.find_elements(By.CSS_SELECTOR, sel)
        if titles_els:
            break
    if titles_els and len(titles_els)>=len(prices):
        filtered=[]
        for price, t in zip(prices, [x.text for x in titles_els[:len(prices)]]):
            if model.upper() in t.upper():
                filtered.append(price)
        if filtered:
            return {"median":round(statistics.median(filtered)),"count":len(filtered),"prices":filtered}
        else:
            return {"median":None,"count":0}
    else:
        return {"median":round(statistics.median(prices)),"count":len(prices),"prices":prices,"note":"title_unavailable"}

def fetch_yahoo_discount(yahoo_url):
    # yahoo_urlから code を抽出して APIで priceLabel を取得
    try:
        code=yahoo_url.split("/")[-1].replace(".html","")
        # seller_id は y-kojima / y-sofmap を推定
        seller="y-kojima" if "y-kojima" in yahoo_url else "y-sofmap"
        url="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
        params={"appid":CONFIG["YAHOO_APP_ID"],"query":code,"seller_id":seller,"results":1}
        r=requests.get(url, params=params, timeout=10)
        if r.status_code==200:
            hits=r.json().get("hits",[])
            if hits:
                h=hits[0]
                pl=h.get("priceLabel",{})
                default=pl.get("defaultPrice") or h.get("price")
                discount=pl.get("discountedPrice")
                premium=h.get("premiumPrice")
                if discount and default and discount < default:
                    rate=round((default-discount)/default*100,1)
                    return {"has_discount":True,"default":default,"discounted":discount,"rate":rate}
                return {"has_discount":False,"default":default}
    except Exception as e:
        pass
    return {"has_discount":False}

# 入力CSV
import os
in_path="/Users/harkingbee/opne code/project/results/summer_full_dual_20260830_1234.csv"
rows=[]
with open(in_path, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)
# v2では旧で利益あり14件＋信頼度検証のためハンディ全件のうち件数3件未満だったものを再検証（計20件に絞る）
profitable_old=[r for r in rows if str(r["is_profitable"])=="True"]
# 旧でprofit_smallがプラスだったものを追加
others=[r for r in rows if r not in profitable_old and str(r["profit_yen"]).lstrip("-").isdigit() and int(r["profit_yen"])>0]
rows=profitable_old+others
print(f"絞り込み v2対象 {len(rows)}件")
# 20件に制限
rows=rows[:20]

driver=build_driver()
out=[]
for i,row in enumerate(rows):
    name=row["name"]; model=row["model"]; category=row["category"]; net=int(row["net_cost"]) if row["net_cost"] else 0
    # 割引情報
    # 割引取得はAPI制限で時間がかかるため今回はスキップ（v2では件数とタイトルフィルタを優先）
    disc={"has_discount":False, "rate":""}
    # disc=fetch_yahoo_discount(row["yahoo_url"])
    # time.sleep(0.6)
    # メルカリ＋ヤフオクをタイトルフィルタ付きで再取得
    m=fetch_mercari_filtered(model, driver)
    time.sleep(1)
    y=fetch_yahooAuction_filtered(model, driver)
    time.sleep(1)
    m_med=m["median"]; y_med=y["median"]
    # 保守的中央値
    if m_med and y_med:
        cons=min(m_med,y_med)
    elif m_med:
        cons=m_med
    elif y_med:
        cons=y_med
    else:
        cons=None
    shipping=get_shipping(category)
    shipping_small=300 if "ハンディ" in name or "ハンディファン" in name else shipping
    # 利益計算
    if cons:
        fee=cons*CONFIG["MERCARI_FEE"]
        profit=cons-fee-shipping-net
        margin=profit/cons*100 if cons else 0
        profit_small=cons-fee-shipping_small-net
        # 件数閾値：両サイト合計または片方で3件未満は低信頼
        total_cnt=(m["count"] if m["count"] else 0)+(y["count"] if y["count"] else 0)
        is_profitable = profit>0 and margin>=CONFIG["MIN_MARGIN"] and total_cnt>=CONFIG["MIN_COUNT"]
        confidence="high" if total_cnt>=5 else ("medium" if total_cnt>=3 else "low")
    else:
        profit=None; margin=None; profit_small=None; is_profitable=False; confidence="none"; total_cnt=0
    print(f"[{i+1:02d}] {name[:36]} model={model} disc={disc.get('rate') if disc.get('has_discount') else '-'}% m:{m_med}({m['count']}) y:{y_med}({y['count']}) cons{cons} ship{shipping} profit{profit} margin{margin} conf{confidence}")
    out.append({
        "name":name,"model":model,"category":category,"yahoo_price":row["yahoo_price"],"net_cost":net,
        "yahoo_discount_rate":disc.get("rate") if disc.get("has_discount") else "",
        "has_discount":disc.get("has_discount"),
        "mercari_median_v2":m_med,"mercari_count_v2":m["count"],"mercari_note":m.get("note",""),
        "yahooAuction_median_v2":y_med,"yahooAuction_count_v2":y["count"],
        "conservative_median_v2":cons,"shipping_used":shipping,
        "profit_yen_v2":round(profit) if profit is not None else "",
        "profit_margin_v2":round(margin,2) if margin is not None else "",
        "profit_small_post_v2":round(profit_small) if profit_small is not None else "",
        "is_profitable_v2":is_profitable,"confidence":confidence,"total_count_v2":total_cnt,
        "old_conservative":row["conservative_median"],"old_profit":row["profit_yen"],
        "yahoo_url":row["yahoo_url"]
    })

driver.quit()

# 保存
out_path="/Users/harkingbee/opne code/project/results/biccamera_v2_20260830.csv"
with open(out_path,"w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=out[0].keys())
    w.writeheader(); w.writerows(out)
print(f"保存 {out_path}")
# サマリー
prof=[r for r in out if r["is_profitable_v2"]]
print(f"\n=== v2 利益あり(10%+件数3件以上) {len(prof)}/{len(out)} ===")
for r in sorted(prof, key=lambda x: x["profit_yen_v2"] if isinstance(x["profit_yen_v2"], int) else -999, reverse=True):
    print(f"{r['name'][:38]} | cons{r['conservative_median_v2']} net{r['net_cost']} profit{r['profit_yen_v2']} {r['profit_margin_v2']}% cnt{r['total_count_v2']} conf{r['confidence']} disc{r['yahoo_discount_rate']}%")
print("\n=== 旧14件→新判定 ===")
for r in out:
    if str(r["old_profit"]).lstrip("-").isdigit() and int(r["old_profit"])>0:
        print(f"{r['name'][:36]} old{ r['old_profit']}->{r['profit_yen_v2']} conf{r['confidence']}")

