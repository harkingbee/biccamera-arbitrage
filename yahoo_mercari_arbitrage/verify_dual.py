"""
メルカリ+ヤフオク 価格検証（精度向上版）
- モデル型番のみで検索（ブランド汎用検索のノイズを排除）
- タイトルではなく価格のみだが、型番検索は精度が高い
- 両サイトの中央値を併記し、保守的（低い方）で利益再計算
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

CONFIG = {
    "MERCARI_FEE": 0.10,
    "SHIPPING": 600,
    "SHIPPING_SMALL": 300,
}

def build_driver():
    opts = Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument("--window-size=1280,900")
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    d = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    d.implicitly_wait(5)
    return d

def extract_model(name):
    m = re.findall(r"[A-Z0-9\-]{3,}", name.upper())
    # フィルタ：短すぎるものや一般的単語を除外
    candidates = [x for x in m if len(x)>=4 and not x in ["SIROCA","SONY","YAHOO","WHITE","BLACK"]]
    if candidates:
        # 最も長いものを型番とみなす（SF-H751-W は長い）
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return None

def fetch_mercari(model, driver, n=20):
    if not model:
        return None
    url = f"https://www.mercari.com/jp/search/?keyword={quote(model)}&status=sold_out"
    driver.get(url)
    try:
        WebDriverWait(driver, 7).until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.merPrice, [class*='priceContainer']")))
    except:
        time.sleep(2)
    for sel in ["span.merPrice","span[class*='priceContainer']","span[class*='price']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:n]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 100 <= int(txt) <= 300000:
                    prices.append(int(txt))
            if prices:
                return {"prices":prices,"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"count":len(prices)}
    return None

def fetch_yahoo_auction(model, driver, n=20):
    if not model:
        return None
    url = f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url)
    time.sleep(2)
    # ヤフオクの価格セレクタは複数候補
    for sel in ["span[class*='Price__value']","span[class*='Price']","span[class*='price']","p[class*='price']","span[class*='u-textLarge']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            prices=[]
            for el in els[:n]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 100 <= int(txt) <= 500000:
                    # ヤフオクは「1円」出品も多いので最低1000円以上を有効とする
                    if int(txt) < 500:
                        continue
                    prices.append(int(txt))
            if prices:
                return {"prices":prices,"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"count":len(prices)}
    return None

def calc_profit(net, median):
    if median is None:
        return None
    fee = median * CONFIG["MERCARI_FEE"]
    profit = median - fee - CONFIG["SHIPPING"] - net
    margin = profit/median*100 if median else 0
    profit_small = median - fee - CONFIG["SHIPPING_SMALL"] - net
    return {"profit":round(profit),"margin":round(margin,2),"profit_small":round(profit_small)}

# 読み込み：前回のサマーハンディCSV
import os
csv_path = "/Users/harkingbee/opne code/project/results/summer_handy_20260830_1112.csv"
rows=[]
with open(csv_path, encoding="utf-8-sig") as f:
    r=csv.DictReader(f)
    for row in r:
        rows.append(row)
# 検証件数を絞る：旧で利益あり11件 + ハンディファン全件のうち上位15件に限定（時間短縮）
# 旧利益ありを優先
profitable_old = [row for row in rows if row["is_profitable"]=="True"]
handy = [row for row in rows if "ハンディ" in row["name"]]
# 重複排除で15件に
seen=set()
filtered=[]
for lst in [profitable_old, handy]:
    for row in lst:
        if row["name"] not in seen:
            seen.add(row["name"])
            filtered.append(row)
            if len(filtered) >= 15:
                break
    if len(filtered) >= 15:
        break
rows = filtered
print(f"絞り込み後 {len(rows)}件に限定")

driver = build_driver()
print(f"検証対象 {len(rows)}件")
out=[]
for i,row in enumerate(rows):
    name=row["name"]
    y_price=int(row["yahoo_price"]) if row["yahoo_price"] else 0
    net=int(row["net_cost"]) if row["net_cost"] else 0
    old_median=row["mercari_median"]
    model=extract_model(name)
    keyword=model if model else name[:12]
    print(f"\n[{i+1:02d}] {name[:40]} | model={model} kw={keyword}")
    m_data=fetch_mercari(keyword, driver)
    time.sleep(2)
    y_data=fetch_yahoo_auction(keyword, driver)
    time.sleep(2)
    m_med=m_data["median"] if m_data else None
    y_med=y_data["median"] if y_data else None
    # 保守的中央値：両方ある場合は低い方、片方のみならその方
    if m_med and y_med:
        conservative=min(m_med, y_med)
        # 平均的な市場価格としても平均を参考値で持つ
        avg_med=round((m_med+y_med)/2)
    elif m_med:
        conservative=m_med
        avg_med=m_med
    elif y_med:
        conservative=y_med
        avg_med=y_med
    else:
        conservative=None
        avg_med=None
    profit_info=calc_profit(net, conservative) if conservative else None
    old_profit=row["profit_yen"]
    print(f"  old median {old_median} -> new mercari {m_med} ({m_data['count'] if m_data else 0}件) / yahooAuction {y_med} ({y_data['count'] if y_data else 0}件) => conservative {conservative}")
    if profit_info:
        print(f"  net {net} profit {profit_info['profit']} margin {profit_info['margin']}% (small {profit_info['profit_small']})")
    out.append({
        "name":name,"model":model,"yahoo_price":y_price,"net_cost":net,
        "old_median":old_median,"old_profit":old_profit,
        "mercari_median_new":m_med,"mercari_count":m_data["count"] if m_data else 0,
        "yahooAuction_median":y_med,"yahooAuction_count":y_data["count"] if y_data else 0,
        "conservative_median":conservative,"avg_median":avg_med,
        "new_profit":profit_info["profit"] if profit_info else "",
        "new_margin":profit_info["margin"] if profit_info else "",
        "new_profit_small":profit_info["profit_small"] if profit_info else "",
        "yahoo_url":row["yahoo_url"],
        "is_profitable_new": profit_info["profit"]>0 and profit_info["margin"]>=10 if profit_info else False
    })

driver.quit()

# 保存
out_path="/Users/harkingbee/opne code/project/results/verify_dual_20260830.csv"
with open(out_path,"w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=out[0].keys())
    w.writeheader(); w.writerows(out)
print(f"\n保存: {out_path}")

# サマリー表示
profitable=[r for r in out if r["is_profitable_new"]]
print(f"\n=== 検証後 利益あり(10%以上) {len(profitable)}/{len(out)}件 ===")
for r in sorted(profitable, key=lambda x: x["new_profit"] if x["new_profit"]!="" else -999, reverse=True)[:10]:
    print(f"{r['name'][:38]} | net¥{r['net_cost']:,} median¥{r['conservative_median']:,} (mercari {r['mercari_median_new']} yahoo {r['yahooAuction_median']}) profit¥{r['new_profit']:,} {r['new_margin']}%")

print("\n=== 旧TOP11の再評価 ===")
# 旧で利益ありとされていた11件を再評価
old_top_names=["SF-H751-W","SF-H751-CH","SF-H631PL","SF-H631HS","SF-H751-AD","SF-H631AG","ACP5-ORH","RLX-MP023-C","RLX-MP023-H","W3800511"]
for r in out:
    if any(n in (r["model"] or "") for n in old_top_names):
        print(f"{r['name'][:36]} | old profit {r['old_profit']} oldMed {r['old_median']} -> newMed {r['conservative_median']} newProfit {r['new_profit']} newMargin {r['new_margin']}")

# ハンディファン0件問題の説明
no_data=[r for r in out if not r["mercari_median_new"] and not r["yahooAuction_median"]]
print(f"\n両サイトともデータなし: {len(no_data)}件 (取引履歴なしの新商品や型番不一致)")
for r in no_data[:5]:
    print(f"  {r['name'][:40]} model={r['model']}")

