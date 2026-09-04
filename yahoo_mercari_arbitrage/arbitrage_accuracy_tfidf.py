"""
GitHub精度向上 v3：scikit-learn (GitHub: scikit-learn/scikit-learn) TF-IDF + 保守的中央値 + 価格履歴
- 従来の単純 model in title ではなく、TF-IDFコサイン類似度で照合
- 価格履歴（GitHubにコミットされた過去CSV）からボラティリティでフィルタ（stdev/mean>0.3は除外）
- 入出力は repo ルートからの相対パス（results/）に統一。ローカル絶対パスを持たないためCIでも動く
"""
import csv, glob, os, statistics, unicodedata
import asyncio
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yahoo_mercari_arbitrage.mercapi_utils import fetch_mercapi_median

RESULTS_DIR = "results"
VOLATILITY_THRESHOLD = 0.3  # stdev/mean がこれを超える型番は相場が不安定とみなし除外


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    for ch in ("−", "－", "‐", "‑"):
        s = s.replace(ch, "-")
    return s.lower()


def tfidf_match_score(titles, query, threshold=0.35):
    """TF-IDFでqueryに最も類似するタイトルのスコアとインデックスを返す"""
    if not titles:
        return 0, -1
    docs = [_norm(query)] + [_norm(t) for t in titles]
    try:
        vec = TfidfVectorizer(token_pattern=r"[a-z0-9]{2,}", ngram_range=(1, 2))
        mat = vec.fit_transform(docs)
        sims = cosine_similarity(mat[0:1], mat[1:])[0]
        idx = int(sims.argmax())
        return float(sims[idx]), idx
    except Exception:
        return 0, -1


def latest_file(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def load_price_history(exclude_path=None):
    """過去のgithub_integrated CSVから同一モデルの conservative_median 推移を集計"""
    history = {}
    for hf in glob.glob(os.path.join(RESULTS_DIR, "github_integrated_*.csv")):
        if hf == exclude_path:
            continue
        try:
            with open(hf, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    m = r.get("model")
                    if not m:
                        continue
                    try:
                        cons = int(r.get("conservative_median") or 0)
                        if cons:
                            history.setdefault(m, []).append(cons)
                    except (TypeError, ValueError):
                        pass
        except OSError:
            pass
    return history


def volatility(prices):
    if len(prices) < 2:
        return 0
    mean = statistics.mean(prices)
    return statistics.stdev(prices) / mean if mean else 0


async def check_model(model, yahoo_title, history):
    m_data = await fetch_mercapi_median(model)
    titles = (m_data or {}).get("titles", [])
    score, _ = tfidf_match_score(titles, yahoo_title)
    hist = history.get(model, [])
    vol = volatility(hist)
    return score, vol, titles[:3]


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    in_path = latest_file(os.path.join(RESULTS_DIR, "target_products_*.csv"))
    if not in_path:
        print(f"対象ファイルなし（{RESULTS_DIR}/target_products_*.csv）。精度モニタをスキップします")
        return

    rows = []
    with open(in_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"対象 {len(rows)}件をTF-IDF+ボラティリティで再評価 (入力: {in_path})")

    history = load_price_history()

    out = []
    for r in rows:
        model = r.get("model")
        yahoo_title = r.get("product_name", "")
        if not model:
            print(f"{'(model無し)':15} -> SKIP")
            out.append({**r, "tfidf_score": 0, "volatility": 0, "new_match": False})
            continue
        score, vol, sample_titles = await check_model(model, yahoo_title, history)
        vol_ok = vol <= VOLATILITY_THRESHOLD
        new_match = score >= 0.35 and vol_ok
        reason = "PASS" if new_match else ("FILTER(高ボラ)" if score >= 0.35 and not vol_ok else "FILTER")
        sample = sample_titles[0][:30] if sample_titles else "none"
        print(f"{model:15} score{score:.2f} vol{vol:.2f} sample:{sample:30} -> {reason}")
        out.append({**r, "tfidf_score": round(score, 3), "volatility": round(vol, 3), "new_match": new_match})
        await asyncio.sleep(0.3)

    if not out:
        print("評価対象0件のため保存をスキップ")
        return

    today = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(RESULTS_DIR, f"accuracy_tfidf_{today}.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"\n保存 {out_path}")

    passed = [r for r in out if r["new_match"]]
    print(f"\nTF-IDF+ボラティリティ PASS {len(passed)}/{len(out)} (score>=0.35 かつ vol<={VOLATILITY_THRESHOLD})")
    for r in passed:
        print(f" {r.get('model')} score{r['tfidf_score']} vol{r['volatility']} profit{r.get('profit_yen')}")


if __name__ == "__main__":
    asyncio.run(main())
