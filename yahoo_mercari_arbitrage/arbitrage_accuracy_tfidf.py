"""
GitHub精度向上 v3：scikit-learn (GitHub: scikit-learn/scikit-learn) TF-IDF + 保守的中央値 + 価格履歴
- 従来の単純 model in title ではなく、TF-IDFコサイン類似度で照合
- 価格履歴（GitHubにコミットされた過去CSV）からボラティリティでフィルタ
"""
import csv, os, re, statistics, time, asyncio, unicodedata
from datetime import datetime
import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData

def _norm(s):
    s=unicodedata.normalize("NFKC", s or "")
    for ch in ("\u2212","\uff0d","\u2010","\u2011"): s=s.replace(ch,"-")
    return s.lower()

def tfidf_match_score(titles, query, threshold=0.35):
    """TF-IDFでqueryに最も類似するタイトルのスコアとインデックスを返す"""
    if not titles: return 0, -1
    docs=[_norm(query)] + [_norm(t) for t in titles]
    try:
        vec=TfidfVectorizer(token_pattern=r"[a-z0-9]{2,}", ngram_range=(1,2))
        mat=vec.fit_transform(docs)
        sims=cosine_similarity(mat[0:1], mat[1:])[0]
        idx=int(sims.argmax())
        return float(sims[idx]), idx
    except:
        return 0, -1

CONFIG={"YAHOO_APP_ID":"dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw","YAHOO_POINT_RATE":0.10,"COUPON_DISCOUNT":1000,"PAYPAY_RATE":0.01,"MIN_MARGIN":0.10,"MIN_COUNT":3,"MERCARI_FEE":0.10,"MIN_PRICE":2000}
TODAY=datetime.now().strftime("%Y%m%d_%H%M")

# 簡易デモ：既存のtarget_productsのタイトル類似度を再評価
in_path="/Users/harkingbee/opne code/project/results/target_products_20260831.csv"
rows=[]
with open(in_path, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)

print(f"対象 {len(rows)}件をTF-IDFで再評価")
# 価格履歴：過去のgithub_integrated CSVから同一モデルの価格推移を取得
import glob
history_files=glob.glob("/Users/harkingbee/opne code/project/results/github_integrated_*.csv")
history={}
for hf in history_files:
    try:
        with open(hf, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                m=r.get("model")
                if not m: continue
                try:
                    cons=int(r.get("conservative_median") or 0)
                    if cons:
                        history.setdefault(m, []).append(cons)
                except: pass
    except: pass

# GitHub mercapiで最新のタイトルも取得してTF-IDFで照合デモ
async def check_model(model, yahoo_title):
    m=Mercapi()
    try:
        res=await m.search(model, status=[SearchRequestData.Status.STATUS_SOLD_OUT])
        if not res.items:
            return 0, 0, []
        titles=[x.name for x in res.items[:10]]
        score, idx = tfidf_match_score(titles, yahoo_title)
        # 価格履歴のボラティリティ
        hist=history.get(model, [])
        vol=0
        if len(hist)>=2:
            vol=statistics.stdev(hist)/statistics.mean(hist) if statistics.mean(hist) else 0
        return score, vol, titles[:3]
    except Exception as e:
        return 0, 0, []

async def main():
    out=[]
    for r in rows:
        model=r["model"]; yahoo_title=r["product_name"]
        score, vol, sample_titles = await check_model(model, yahoo_title)
        # 旧は単純 model in title、今回はTF-IDF 0.35以上で合格
        old_match=True # 旧は常にTrue
        new_match=score>=0.35
        print(f"{model:15} score{score:.2f} vol{vol:.2f} sample:{sample_titles[0][:30] if sample_titles else 'none':30} -> {'PASS' if new_match else 'FILTER'} (old PASS)")
        out.append({**r, "tfidf_score":round(score,3), "volatility":round(vol,3), "new_match":new_match})
        await asyncio.sleep(1)

    # 保存
    out_path="/Users/harkingbee/opne code/project/results/accuracy_tfidf_20260901.csv"
    with open(out_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    print(f"\n保存 {out_path}")
    # サマリー
    passed=[r for r in out if r["new_match"]]
    print(f"\nTF-IDF PASS {len(passed)}/{len(out)} (threshold 0.35)")
    for r in passed:
        print(f" {r['model']} {r['tfidf_score']} vol{r['volatility']} profit{r['profit_yen']}")

asyncio.run(main())
