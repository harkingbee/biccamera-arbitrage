"""
v2フル：29件をタイトルフィルタ＋件数閾値＋ボット回避で再検証
逐次保存で中断耐性
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

CONFIG={"MERCARI_FEE":0.10,"MIN_MARGIN":10,"MIN_COUNT":3}
SHIPPING_MAP={"扇風機本体":600,"除湿機":1200,"家庭用エアコン":3000,"その他":600}
def get_shipping(cat):
    for k,v in SHIPPING_MAP.items():
        if k in cat: return v
    return 600

def build_driver():
    opts=Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches",["enable-automation"])
    opts.add_experimental_option("useAutomationExtension",False)
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    d.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    d.implicitly_wait(4)
    return d

def is_bot_page(driver):
    t=driver.title or ""
    src=driver.page_source[:2000]
    return "しばらくお待ちください" in t or "しばらくお待ちください" in src or "Please wait" in src

def fetch_mercari_filtered(model, driver):
    if not model:
        return {"median":None,"count":0,"note":"no_model"}
    url=f"https://www.mercari.com/jp/search/?keyword={quote(model)}&status=sold_out"
    for attempt in range(2):
        driver.get(url)
        time.sleep(3)
        if is_bot_page(driver):
            print(f"  bot detected, retry {attempt+1}")
            time.sleep(5)
            continue
        try:
            WebDriverWait(driver,4).until(EC.presence_of_element_located((By.CSS_SELECTOR,"span.merPrice")))
        except:
            time.sleep(1)
        prices_els=driver.find_elements(By.CSS_SELECTOR,"span.merPrice, span[class*='priceContainer']")
        titles_els=[]
        for sel in ["[data-testid='itemName']","div[class*='ItemName']"]:
            titles_els=driver.find_elements(By.CSS_SELECTOR, sel)
            if titles_els: break
        prices_raw=[]
        for el in prices_els[:20]:
            txt=re.sub(r"[^\d]","",el.text)
            if txt and 500 <= int(txt) <= 500000:
                prices_raw.append(int(txt))
        if not prices_raw:
            return {"median":None,"count":0,"note":"no_prices"}
        if titles_els and len(titles_els)==len(prices_els):
            filtered=[]
            for price, t in zip(prices_raw, [x.text for x in titles_els[:len(prices_raw)]]):
                if model.upper() in t.upper():
                    filtered.append(price)
            if filtered:
                return {"median":round(statistics.median(filtered)),"count":len(filtered),"note":"filtered"}
            else:
                return {"median":None,"count":0,"note":"title_mismatch"}
        else:
            return {"median":round(statistics.median(prices_raw)),"count":len(prices_raw),"note":"title_unavailable"}
    return {"median":None,"count":0,"note":"bot_blocked"}

def fetch_yahoo_filtered(model, driver):
    if not model or model=="SWITCHBOT":
        return {"median":None,"count":0}
    url=f"https://auctions.yahoo.co.jp/search/search?p={quote(model)}&va={quote(model)}&exflg=1&b=1&n=50&s1=end&o1=d"
    driver.get(url)
    time.sleep(2)
    prices=[]
    for sel in ["span[class*='Price']","span[class*='price']"]:
        els=driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            for el in els[:20]:
                txt=re.sub(r"[^\d]","",el.text)
                if txt and 500 <= int(txt) <= 500000:
                    prices.append(int(txt))
            break
    if not prices:
        return {"median":None,"count":0}
    titles_els=[]
    for sel in ["a.Product__title","h3.Product__title"]:
        titles_els=driver.find_elements(By.CSS_SELECTOR, sel)
        if titles_els: break
    if titles_els and len(titles_els)>=len(prices):
        filtered=[]
        for price, t in zip(prices, [x.text for x in titles_els[:len(prices)]]):
            if model.upper() in t.upper():
                filtered.append(price)
        if filtered:
            return {"median":round(statistics.median(filtered)),"count":len(filtered)}
        else:
            return {"median":None,"count":0}
    else:
        return {"median":round(statistics.median(prices)),"count":len(prices),"note":"title_unavailable"}

in_path="/Users/harkingbee/opne code/project/results/summer_full_dual_20260830_1234.csv"
out_path="/Users/harkingbee/opne code/project/results/biccamera_v2_full_20260830.csv"
# 読み込み
rows=[]
with open(in_path, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)
print(f"入力 {len(rows)}件")
# ヘッダー
cols=["name","model","category","yahoo_price","net_cost","mercari_median_v2","mercari_count_v2","mercari_note","yahooAuction_median_v2","yahooAuction_count_v2","conservative_v2","shipping","profit_v2","margin_v2","profit_small_v2","is_profitable_v2","confidence","total_count","yahoo_url","old_conservative","old_profit"]
with open(out_path,"w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=cols)
    w.writeheader()

driver=build_driver()
print("driver built")
out=[]
for i,row in enumerate(rows):
    name=row["name"]; model=row["model"]; cat=row["category"]; net=int(row["net_cost"]) if row["net_cost"] else 0
    print(f"[{i+1:02d}/{len(rows)}] {name[:32]} model={model}")
    m=fetch_mercari_filtered(model, driver)
    time.sleep(1)
    y=fetch_yahoo_filtered(model, driver)
    time.sleep(1)
    m_med=m["median"]; y_med=y["median"]
    if m_med and y_med:
        cons=min(m_med,y_med)
    elif m_med:
        cons=m_med
    elif y_med:
        cons=y_med
    else:
        cons=None
    ship=get_shipping(cat)
    ship_small=300 if "ハンディ" in name else ship
    if cons:
        fee=cons*CONFIG["MERCARI_FEE"]
        profit=cons-fee-ship-net
        margin=profit/cons*100 if cons else 0
        profit_small=cons-fee-ship_small-net
        total=(m["count"] or 0)+(y["count"] or 0)
        is_prof= profit>0 and margin>=CONFIG["MIN_MARGIN"] and total>=CONFIG["MIN_COUNT"]
        conf="high" if total>=5 else ("medium" if total>=3 else "low")
    else:
        profit=margin=profit_small=None; is_prof=False; conf="none"; total=0
    out_row={
        "name":name,"model":model,"category":cat,"yahoo_price":row["yahoo_price"],"net_cost":net,
        "mercari_median_v2":m_med,"mercari_count_v2":m["count"],"mercari_note":m.get("note",""),
        "yahooAuction_median_v2":y_med,"yahooAuction_count_v2":y["count"],
        "conservative_v2":cons,"shipping":ship,
        "profit_v2":round(profit) if profit is not None else "",
        "margin_v2":round(margin,2) if margin is not None else "",
        "profit_small_v2":round(profit_small) if profit_small is not None else "",
        "is_profitable_v2":is_prof,"confidence":conf,"total_count":total,
        "yahoo_url":row["yahoo_url"],"old_conservative":row["conservative_median"],"old_profit":row["profit_yen"]
    }
    out.append(out_row)
    with open(out_path,"a",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=cols)
        w.writerow(out_row)
    print(f" -> cons{cons} m{m_med}({m['count']}) y{y_med}({y['count']}) profit{out_row['profit_v2']} conf{conf}")
    # 429対策で間隔
    time.sleep(0.5)

driver.quit()
prof=[r for r in out if r["is_profitable_v2"]]
print(f"\n=== v2フル 利益あり {len(prof)}/{len(out)} ===")
for r in sorted(prof, key=lambda x: x["profit_v2"] if isinstance(x["profit_v2"], int) else -999, reverse=True)[:10]:
    print(f"{r['name'][:38]} cons{r['conservative_v2']} profit{r['profit_v2']} {r['margin_v2']}% cnt{r['total_count']} {r['confidence']}")
print(f"保存 {out_path}")
