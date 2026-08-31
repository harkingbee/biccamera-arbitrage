import csv
rows=[]
with open("/Users/harkingbee/opne code/project/results/summer_full_dual_20260830_1234.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)
rows=rows[:2]
print(f"test {len(rows)} rows")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
opts=Options()
opts.add_argument('--headless=new'); opts.add_argument('--no-sandbox'); opts.add_argument('--disable-dev-shm-usage'); opts.add_argument('--disable-gpu'); opts.add_argument('--window-size=1280,900')
opts.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
print("building driver")
d=webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
print("driver built")
d.get("https://www.mercari.com/jp/search/?keyword=SF-H631HS&status=sold_out")
print("got mercari", d.title[:30])
d.quit()
print("done")
