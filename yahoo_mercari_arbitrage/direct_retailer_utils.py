"""
ノジマオンライン / ヨドバシ.com の直接検索結果を無料で取得する共有ラッパー。

背景：両サイトともAkamai/WAFにより、通常のrequests/Playwrightのブラウザ自動化は
403 "Access Denied" 等でブロックされる。調査の結果、ブロックの主因はブラウザ自動化の
JS的な特徴（navigator.webdriver等）ではなく、TLSハンドシェイクのフィンガープリント
（JA3等）でbotと判定されている可能性が高いことが分かった。curl_cffi
（GitHub: lexiforest/curl_cffi、libcurl-implから実際のChromeのTLS/HTTP2挙動を
再現するインパーソネーション機能を持つ）でリクエストするだけで、有料プロキシ無しに
高確率で正規のHTMLが取得できる。

ただしAkamai側の判定は確率的/セッションベースで、ノジマは体感50〜60%程度の割合で
403が返る（ヨドバシは今のところ安定して200が返る）。そのためリトライ+バックオフを
必須とし、既定の試行回数でも取得できない場合は呼び出し側でYahoo代理検索へ
フォールバックすること。
"""
import logging
import random
import re
import time

logger = logging.getLogger("direct_retailer")

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

_UA_IMPERSONATE = "chrome124"
_BLOCK_MARKERS = ("Access Denied", "Reference #")


def _get_with_retry(url, retries=6, base_delay=2.0):
    """curl_cffiでTLSフィンガープリントを偽装して取得。ブロック検知時はバックオフ+ジッターで再試行。"""
    if not CURL_CFFI_AVAILABLE:
        logger.warning("curl_cffi未インストールのため直接取得をスキップ")
        return None
    for attempt in range(1, retries + 1):
        try:
            r = curl_requests.get(url, impersonate=_UA_IMPERSONATE, timeout=15)
            if r.status_code == 200 and not any(m in r.text[:300] for m in _BLOCK_MARKERS):
                return r.text
            logger.info(f" ブロック検知(status={r.status_code}) attempt={attempt} url={url[:60]}")
        except Exception as e:
            logger.warning(f" err attempt={attempt} {e}")
        if attempt < retries:
            time.sleep(base_delay * attempt + random.uniform(0, 1.5))
    return None


# ---------------- ノジマオンライン ----------------

def fetch_nojima(query, results=8):
    """ノジマオンラインの検索結果を直接取得しYahoo hit互換の辞書リストで返す"""
    from urllib.parse import quote
    url = f"https://online.nojima.co.jp/app/catalog/list/init?searchMethod=0&searchWord={quote(query)}"
    html = _get_with_retry(url)
    if not html:
        logger.warning(f" ノジマ直接取得failed(全リトライ失敗): {query}")
        return []
    blocks = re.findall(
        r'<div class="shouhinlist">.*?(?=<div class="shouhinlist">|<div class="commoditylistitem"|\Z)',
        html, re.S,
    )
    hits = []
    for b in blocks[:results]:
        m_url = re.search(r'href="(/commodity/\d+/(\d+)/)"', b)
        m_name = re.search(r'<img class="main"[^>]*alt="([^"]+)"', b)
        m_price = re.search(r'<span class="price">\s*([\d,]+)円', b)
        m_point = re.search(r'pointKangen">(\d+)円分', b)
        if not (m_url and m_name and m_price):
            continue
        price = int(m_price.group(1).replace(",", ""))
        point = int(m_point.group(1)) if m_point else 0
        hits.append({
            "name": m_name.group(1),
            "price": price,
            "url": "https://online.nojima.co.jp" + m_url.group(1),
            "code": m_url.group(2),
            "seller_name": "ノジマオンライン",
            "category": "",
            "point_rate": round(point / price, 4) if price else 0.0,
        })
    logger.info(f" ノジマ直接取得 query={query} -> {len(hits)}件")
    return hits


# ---------------- ヨドバシ.com ----------------

def fetch_yodobashi(query, results=8):
    """ヨドバシ.comの検索結果を直接取得しYahoo hit互換の辞書リストで返す"""
    from urllib.parse import quote
    url = f"https://www.yodobashi.com/?word={quote(query)}"
    html = _get_with_retry(url)
    if not html:
        logger.warning(f" ヨドバシ直接取得failed(全リトライ失敗): {query}")
        return []
    head_idx = html.find("searchResultsHead")
    results_html = html[head_idx:] if head_idx != -1 else html
    matches = list(re.finditer(r'data-sku="(\d+)" class="srcResultItem_block', results_html))
    hits = []
    for i, m in enumerate(matches[:results]):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else start + 3000
        b = results_html[start:end]
        m_href = re.search(r'href="(/product/\d+/)"', b)
        m_pname = re.search(r'<div class="pName[^"]*"><p>([^<]*)</p><p>([^<]*)</p>', b)
        m_price = re.search(r'class="productPrice">￥([\d,]+)', b)
        m_point = re.search(r'goldPoint[^>]*>(\d+)', b)
        if not (m_href and m_pname and m_price):
            continue
        price = int(m_price.group(1).replace(",", ""))
        point = int(m_point.group(1)) if m_point else 0
        hits.append({
            "name": f"{m_pname.group(1)} {m_pname.group(2)}".strip(),
            "price": price,
            "url": "https://www.yodobashi.com" + m_href.group(1),
            "code": m.group(1),
            "seller_name": "ヨドバシ.com",
            "category": "",
            "point_rate": round(point / price, 4) if price else 0.0,
        })
    logger.info(f" ヨドバシ直接取得 query={query} -> {len(hits)}件")
    return hits
