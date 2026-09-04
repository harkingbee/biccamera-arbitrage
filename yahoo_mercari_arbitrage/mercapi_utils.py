"""
GitHub take-kun/mercapi 共有ラッパー
- ブロック（「しばらくお待ちください」等）対策：指数バックオフ+ジッター
- 同一モデルの重複問い合わせを1実行内でキャッシュし、リクエスト総数を削減
"""
import asyncio
import random
import statistics

from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData

_client = Mercapi()
_cache = {}

BLOCKED_MARKERS = ("しばらくお待ち", "429", "blocked", "Blocked", "too many")


async def fetch_mercapi_median(model, max_items=20, retries=3, base_delay=2.0):
    """モデル名でメルカリ売切相場を取得。ブロック検知時は長めに待って再試行する。"""
    if not model:
        return {"median": None, "count": 0, "note": "no_model"}
    if model in _cache:
        return _cache[model]

    result = {"median": None, "count": 0, "note": "error"}
    for attempt in range(1, retries + 1):
        try:
            res = await _client.search(model, status=[SearchRequestData.Status.STATUS_SOLD_OUT])
            if not res.items:
                result = {"median": None, "count": 0, "note": "no_sold"}
            else:
                prices = [item.price for item in res.items[:max_items]]
                titles = [item.name for item in res.items[:max_items]]
                result = {
                    "median": round(statistics.median(prices)),
                    "mean": round(statistics.mean(prices)),
                    "count": len(res.items),
                    "total_found": getattr(res.meta, "num_found", len(res.items)),
                    "prices": prices,
                    "titles": titles,
                    "note": "ok",
                }
            break
        except Exception as e:
            is_blocked = any(marker in str(e) for marker in BLOCKED_MARKERS)
            if attempt >= retries:
                result = {"median": None, "count": 0, "note": f"error:{type(e).__name__}"}
                break
            wait = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            if is_blocked:
                wait *= 2
            await asyncio.sleep(wait)

    _cache[model] = result
    return result


def cache_stats():
    return {"cached_models": len(_cache), "hits_saved": sum(1 for v in _cache.values())}
