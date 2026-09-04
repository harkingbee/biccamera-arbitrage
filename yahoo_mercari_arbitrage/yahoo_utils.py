"""
Yahoo Shopping API V3 itemSearch 共有ヘルパー
- 429対策：セッション再利用 + 呼び出し間隔にジッターを入れたレートリミッタ
- yahoo_proxy（ノジマ/ヨドバシ/ケーズ等、直取得不可の小売用の代理検索）は
  小売名ごとに同一クエリを何度も叩いていたのを1クエリ1回に集約（バッチ化）し、
  結果を該当する全ての小売ラベルへ複製する。これによりAPI呼び出し回数を
  yahoo_proxy小売数分の1に削減できる。
"""
import logging
import random
import time

import requests

logger = logging.getLogger("yahoo_utils")

_session = requests.Session()
_last_call_ts = 0.0
_MIN_INTERVAL = 1.0  # 呼び出し間の最低間隔（秒）。ジッターを加えて分散させる。

ITEM_SEARCH_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"


def _throttle():
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    wait = _MIN_INTERVAL - elapsed + random.uniform(0, 0.6)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def fetch_yahoo_seller(app_id, seller_id, query, results=8, price_from=None, retries=3):
    """1クエリぶんのitemSearch結果を取得。429は指数バックオフ+ジッターで再試行。"""
    params = {"appid": app_id, "query": query, "results": results, "sort": "-review_count"}
    if price_from is not None:
        params["price_from"] = price_from
    if seller_id and seller_id != "yahoo_proxy":
        params["seller_id"] = seller_id
        params["in_stock"] = "true"

    for attempt in range(1, retries + 1):
        _throttle()
        try:
            r = _session.get(ITEM_SEARCH_URL, params=params, timeout=15)
            if r.status_code == 429:
                wait = 10 * attempt + random.uniform(0, 3)
                logger.warning(f"429 seller={seller_id} query={query} wait {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            hits = r.json().get("hits", [])
            logger.info(f" seller={seller_id} query={query[:20]} -> {len(hits)}")
            return hits
        except Exception as e:
            logger.warning(f" err seller={seller_id} query={query}: {e}")
            time.sleep(2 ** attempt)
    return []


def fetch_retailers_batched(app_id, retailers, queries, results_per_query, price_from=None):
    """
    retailers: {小売名: [seller_id, ...]} の辞書。
    seller_id=="yahoo_proxy" の小売は横断的に重複しているクエリを1回のAPI呼び出しに
    まとめ、結果を該当する全小売名に複製して返す（呼び出し回数を削減）。

    戻り値: [(hit_dict, retailer_name, query), ...]
    """
    out = []
    proxy_retailers = [r for r, sellers in retailers.items() if sellers == ["yahoo_proxy"]]
    direct_retailers = {r: s for r, s in retailers.items() if s != ["yahoo_proxy"]}

    for retailer, sellers in direct_retailers.items():
        for seller in sellers:
            for q in queries:
                hits = fetch_yahoo_seller(app_id, seller, q, results_per_query, price_from)
                for h in hits:
                    out.append((h, retailer, q))

    if proxy_retailers:
        for q in queries:
            hits = fetch_yahoo_seller(app_id, "yahoo_proxy", q, results_per_query, price_from)
            for h in hits:
                for retailer in proxy_retailers:
                    out.append((h, retailer, q))
        logger.info(
            f"yahoo_proxy集約: {len(queries)}クエリ x1回 -> {len(proxy_retailers)}小売分に複製"
            f"(従来は{len(queries)}クエリ x{len(proxy_retailers)}小売回の個別呼び出しだった)"
        )

    return out
