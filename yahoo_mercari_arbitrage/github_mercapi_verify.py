"""
GitHubプログラム活用：take-kun/mercapi でメルカリ売切価格を正確取得
https://github.com/take-kun/mercapi
Seleniumのボット判定を回避し、APIレベルで売切価格を取得
"""
import csv, asyncio, statistics
from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData

async def fetch_mercapi(model):
    if not model:
        return None
    m=Mercapi()
    try:
        res=await m.search(model, status=[SearchRequestData.Status.STATUS_SOLD_OUT])
        if not res.items:
            return None
        prices=[item.price for item in res.items[:20]]
        return {"median":round(statistics.median(prices)),"mean":round(statistics.mean(prices)),"count":len(res.items),"total_found":res.meta.num_found,"prices":prices}
    except Exception as e:
        print(f" mercapi error {model}: {e}")
        return None

async def main():
    in_path="/Users/harkingbee/opne code/project/results/summer_full_dual_20260830_1234.csv"
    rows=[]
    with open(in_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    # 上位15件をGitHub mercapiで再検証
    target=rows[:15]
    print(f"GitHub mercapiで再検証 {len(target)}件")
    out=[]
    for row in target:
        model=row["model"]
        y_price=int(row["yahoo_price"]) if row["yahoo_price"] else 0
        net=int(row["net_cost"]) if row["net_cost"] else 0
        print(f"\n[{row['rank']}] {row['name'][:36]} model={model}")
        data=await fetch_mercapi(model)
        if data:
            print(f"  mercapi: median¥{data['median']} mean¥{data['mean']} count{data['count']}/{data['total_found']} prices{data['prices'][:5]}")
            fee=data["median"]*0.10
            profit=data["median"]-fee-600-net
            margin=profit/data["median"]*100 if data["median"] else 0
            is_prof=profit>0 and margin>=10 and data["count"]>=3
            print(f"  -> profit¥{round(profit)} margin{round(margin,1)}% is_prof={is_prof}")
        else:
            print(f"  -> no sold data")
            data={"median":None,"count":0}
            profit=margin=None; is_prof=False
        out.append({**row, "mercapi_median_github":data["median"] if data else "", "mercapi_count_github":data["count"] if data else 0, "profit_github":round(profit) if profit is not None else "", "margin_github":round(margin,1) if margin is not None else "", "is_profitable_github":is_prof})
        await asyncio.sleep(1.5) # レート制限対策

    out_path="/Users/harkingbee/opne code/project/results/github_mercapi_20260830.csv"
    with open(out_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=out[0].keys())
        w.writeheader(); w.writerows(out)
    print(f"\n保存 {out_path}")
    prof=[r for r in out if r["is_profitable_github"]]
    print(f"\n=== GitHub mercapi 利益あり {len(prof)}/{len(out)} ===")
    for r in prof:
        print(f"{r['name'][:38]} | mercapi¥{r['mercapi_median_github']} net¥{r['net_cost']} profit¥{r['profit_github']} {r['margin_github']}%")
    # 比較
    print("\n=== 旧Selenium vs GitHub mercapi ===")
    for r in out:
        old=row["profit_yen"] if (row:=r) else ""
        print(f"{r['name'][:30]} old{ r['conservative_median']}->{r['mercapi_median_github']} profitGithub{r['profit_github']}")

if __name__=="__main__":
    asyncio.run(main())
