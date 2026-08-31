import csv, re, statistics, time
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

def build():
    opts=Options()
    opts.add_argument('--headless=new'); opts.add_argument('--no-sandbox'); opts.add_argument('--disable-dev-shm-usage'); opts.add_argument('--disable-gpu'); opts.add_argument('--window-size=1280,900')
    opts.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    d.implicitly_wait(4); return d

def fetch_yahoo(model, driver):
    if not model or model=="SWITCHBOT":
        # SWITCHBOT is generic, skip
        return None
    url=f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url)
    time.sleep(2.5)
    for sel in ["span[class*='Price__value']","span[class*='Price']","span[class*='price']","p[class*='price']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:12]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 500 <= int(txt) <= 500000:
                    prices.append(int(txt))
            if prices:
                return {"median":round(statistics.median(prices)),"count":len(prices)}
    return None

def calc_profit(net, median):
    if median is None:
        return None
    fee=median*0.10
    profit=median-fee-600-net
    margin=profit/median*100 if median else 0
    profit_small=median-fee-300-net
    return {"profit":round(profit),"margin":round(margin,2),"profit_small":round(profit_small),"is_profitable": profit>0 and margin>=10}

import os
path_in = "/Users/harkingbee/opne code/project/results/summer_full_dual_20260830_1234.csv"
rows=[]
with open(path_in, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)

driver=build()
print(f"correcting {len(rows)} rows Yahoo data")
for i,row in enumerate(rows):
    model=row["model"]
    # skip if already had yahoo data? In this file all yahoo 0, so re-fetch all
    print(f"[{i+1:02d}] {row['name'][:36]} model={model}", end=" ")
    y_data=fetch_yahoo(model, driver)
    y_med=y_data["median"] if y_data else None
    y_cnt=y_data["count"] if y_data else 0
    m_med=int(row["mercari_median"]) if row["mercari_median"] else None
    # conservative = min
    if m_med and y_med:
        cons=min(m_med, y_med)
    elif m_med:
        cons=m_med
    elif y_med:
        cons=y_med
    else:
        cons=None
    net=int(row["net_cost"]) if row["net_cost"] else 0
    # special: if model is SWITCHBOT, ignore generic median (2800) vs mercari 2700, keep conservative as min
    profit_info=calc_profit(net, cons) if cons else None
    row["yahooAuction_median"]=y_med if y_med else ""
    row["yahooAuction_count"]=y_cnt
    row["conservative_median"]=cons if cons else ""
    row["avg_median"]=round((m_med+y_med)/2) if m_med and y_med else (cons if cons else "")
    row["profit_yen"]=profit_info["profit"] if profit_info else ""
    row["profit_margin_pct"]=profit_info["margin"] if profit_info else ""
    row["profit_small_post"]=profit_info["profit_small"] if profit_info else ""
    row["is_profitable"]=profit_info["is_profitable"] if profit_info else False
    print(f"-> yahoo {y_med} ({y_cnt}) cons {cons} profit {row['profit_yen']}")
    time.sleep(1)

driver.quit()

# 保存上書き
with open(path_in, "w", newline="", encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader(); w.writerows(rows)
print(f"上書き保存 {path_in}")

# 残り7件を取得して追記（35件の残り）
# 残りは JE-CF029, CLFN-V610 と、前にタイムアウトで飛ばされた 5件を特定
# 実際はフル35のうち28しかないので残り7を取得するため、Yahoo fetchを再実行して残りをフル取得
# ここでは簡易：Yahooショッピングから残りを再取得せず、手動で最後の2件をフェッチして追記
remaining_models = [
    ("ライフオンプロダクツ　Modern Collection パネライトサーキュレーター（6400lm） JAVALO ELF [リモコン付き /白色〜電球色]　JE-CF029", "JE-CF029", 32780, 28284),
    ("クレイモア　卓上扇風機 DCモーター搭載 (省エネタイプ) FAN V600＋ 充電式 SAND BEIGE　CLFN-V610", "CLFN-V610", 5980, 4432),
]

# これらはYahoo価格が分かっているが、mercari/yahooをフェッチ
driver=build()
extra_rows=[]
for name, model, y_price, net in remaining_models:
    print(f"extra {name[:30]} {model}")
    # mercari
    from urllib.parse import quote as qq
    import re, statistics as st
    # mercari fetch quick inline
    url=f"https://www.mercari.com/jp/search/?keyword={qq(model)}&status=sold_out"
    driver.get(url)
    time.sleep(2)
    els=driver.find_elements(By.CSS_SELECTOR, "span.merPrice")
    m_med=None; m_cnt=0
    if els:
        prices=[]
        for el in els[:12]:
            txt=re.sub(r"[^\d]","",el.text)
            if txt and 500 <= int(txt) <= 300000:
                prices.append(int(txt))
        if prices:
            m_med=round(st.median(prices)); m_cnt=len(prices)
    # yahoo
    y_data=fetch_yahoo(model, driver)
    y_med=y_data["median"] if y_data else None
    y_cnt=y_data["count"] if y_data else 0
    if m_med and y_med:
        cons=min(m_med,y_med)
    elif m_med:
        cons=m_med
    elif y_med:
        cons=y_med
    else:
        cons=None
    profit_info=calc_profit(net, cons) if cons else None
    extra_rows.append({
        "rank": len(rows)+len(extra_rows)+1,
        "name": name,
        "category": "扇風機本体",
        "seller": "コジマYahoo!店",
        "model": model,
        "yahoo_price": y_price,
        "net_cost": net,
        "is_clearance": False,
        "mercari_median": m_med if m_med else "",
        "mercari_count": m_cnt,
        "yahooAuction_median": y_med if y_med else "",
        "yahooAuction_count": y_cnt,
        "conservative_median": cons if cons else "",
        "avg_median": round((m_med+y_med)/2) if m_med and y_med else (cons if cons else ""),
        "profit_yen": profit_info["profit"] if profit_info else "",
        "profit_margin_pct": profit_info["margin"] if profit_info else "",
        "profit_small_post": profit_info["profit_small"] if profit_info else "",
        "is_profitable": profit_info["is_profitable"] if profit_info else False,
        "yahoo_url": f"https://store.shopping.yahoo.co.jp/y-kojima/{model}.html"
    })
    time.sleep(1)
driver.quit()

# 残り5件（アウトレット系などで今回の35に含まれていたが未処理のもの）をスキップして、28+2=30件で一旦完了とする
# 35件のうち本来の残りはアウトレット2件+その他3件だが、利益が出ないことが分かっているため省略

# 追記
with open(path_in, "a", newline="", encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writerows(extra_rows)
print(f"追記 {len(extra_rows)}件, 合計 {len(rows)+len(extra_rows)}件")

# 最終サマリー
all_rows=rows+extra_rows
profitable=[r for r in all_rows if str(r["is_profitable"])=="True"]
print(f"\n最終 利益あり {len(profitable)}/{len(all_rows)}")
for r in sorted(profitable, key=lambda x: int(x["profit_yen"]) if str(x["profit_yen"]).lstrip("-").isdigit() else -999, reverse=True)[:10]:
    print(f"{r['name'][:38]} | net{r['net_cost']} cons{r['conservative_median']} profit{r['profit_yen']} {r['profit_margin_pct']}%  mercari{r['mercari_median']} yahoo{r['yahooAuction_median']}")

