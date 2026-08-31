#!/usr/bin/env python3
"""Cross-market margin analysis: Yahoo Auctions (sourcing) vs Suruga-ya (resale).

Matches Yahoo Auctions buy-now sourcing candidates against Suruga-ya used
(second-hand) prices by keyword/title similarity and flags positive-margin
opportunities. This is the LOCAL, self-use foundation for the resale pipeline
(after the Suruga side completes and closes positions / the actual trade is
always manual).

Typical flow:
  1. research_locally.py writes Yahoo candidate CSV/JSON to data/research/
  2. suruga_scraper fetches used-market prices for the same watchlist
  3. this script cross-references and flags arbitrage candidates

Usage:
    python3 -m research.margin_analyzer \
        --yahoo data/research/items_YYYYMMDD.csv \
        --suruga data/suruga/market_YYYYMMDD.json \
        --min-margin-rate 0.20
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import csv
from statistics import median


# ---------- text normalisation ----------

def _norm(s: str) -> str:
    """NFKC-normalise + unify hyphen variants so JP titles/keywords match."""
    s = unicodedata.normalize("NFKC", s or "")
    for ch in ("\u2212", "\uff0d", "\u2010", "\u2011"):
        s = s.replace(ch, "-")
    return s.lower()


def _tokens(s: str) -> set[str]:
    """Meaningful token set from a title (alphanumeric length>=2)."""
    s = _norm(s)
    subs = {
        "ソニー": "sony", "ミラーレス": "mirrorless", "一眼": "camera",
        "カメラ": "camera", "ボディ": "body", "中古": "", "美品": "",
        "未開封": "", "付属": "", "セット": "set", "レンズ": "lens",
    }
    for ja, en in subs.items():
        s = s.replace(ja, en)
    toks = set(re.findall(r"[a-z0-9]{2,}", s))
    stop = {"the", "and", "for", "with", "new", "used", "jp", "yen",
            "cbclass", "イヤ", "シャッター", "ユニット", "交換"}
    return toks - stop


def _match_score(t1: str, t2: str) -> float:
    """Jaccard-like overlap of token sets, 0..1."""
    a, b = _tokens(t1), _tokens(t2)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / max(len(a), len(b))


_MODEL_RE = re.compile(r"ilce[-\s]?([a-z0-9]+)")


# Accessories list compatible bodies (e.g. "EOS R5 / R6 Mark II 用"), so a
# token match against a body title is meaningless. Exclude by marker.
_ACCESSORY_MARKERS = [
    "グリップ", "バッテリー", "カバー", "ストラップ", "ケース", "三脚",
    "クリーナー", "レンズキャップ", "目当て", "アイカップ", "ストロボ",
    "グリップ", "BG-", "充電", "アダプタ", "電池", "保護フィルム",
    "ハンドストラップ", "ネックストラップ", "プレート", "プレーム", "ホルダー",
    "ブラケット", "モノポッド", "シューティンググリップ",
]


def _is_accessory(t1: str, t2: str) -> bool:
    """True when EITHER title is an accessory.

    Accessories list compatible cameras ("EOS R5 / R6 Mark II 用") and don't
    trade against a body price, so any accessory involvement makes the pair
    incomparable for resale research. Only core items (bodies, lenses) count."""
    n1, n2 = _norm(t1), _norm(t2)
    return any(m in n1 or m in n2 for m in _ACCESSORY_MARKERS)


def _model_code(s: str) -> str:
    """Extract a model/SKU code (e.g. ILCE-7CM2 -> 7cm2) if present."""
    m = _MODEL_RE.search(_norm(s))
    return m.group(1) if m else ""


def _same_sku(t1: str, t2: str) -> bool:
    """True if both titles carry a model code AND they match.

    When both sides expose an SKU (e.g. ILCE-7CM2), a match is only valid if
    the codes are identical — prevents cross-model false positives (α7C II
    vs α7R IV). If either side has no code, we fall back to token overlap."""
    c1, c2 = _model_code(t1), _model_code(t2)
    if c1 and c2:
        return c1 == c2
    return True


# ---------- dataclasses ----------

@dataclass
class YahooCandidate:
    item_id: str
    title: str
    buy_now: int  # sourcing cost
    current: int
    bid_count: int
    url: str
    keyword: str


@dataclass
class SurugaPrice:
    title: str
    used: int | None
    new: int | None
    url: str
    keyword: str = ""
    source: str = "suruga"  # "suruga" (asking/retail, higher bias) | "mercari" (resale asking, closer to real comps)


@dataclass
class ArbitrageHit:
    keyword: str
    yahoo_title: str
    yahoo_url: str
    sourcing_cost: int
    resale_price: int
    margin_yen: int
    margin_rate: float
    match_score: float
    source_title: str
    source_url: str
    source_type: str = "suruga"     # which marketplace provided the resale reference
    conservative_price: int = 0     # low-bound resale comp (best matching MERCARI, or suruga if none)
    conservative_rate: float = 0.0  # margin rate using conservative_price
    hits_count: int = 1             # how many resale references statistically backed this match
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------- loaders ----------

def load_yahoo_candidates(path: Path, min_rate: float) -> list[YahooCandidate]:
    cands: list[YahooCandidate] = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            buy_now = row.get("buyNowPrice") or row.get("buy_now_price")
            cur = row.get("currentPrice") or row.get("current_price") or "0"
            buy_now = _to_int(buy_now)
            if buy_now is None:
                continue  # no fixed sourcing price -> not a sourcing candidate
            cands.append(YahooCandidate(
                item_id=row.get("itemId", ""),
                title=row.get("title", ""),
                buy_now=buy_now,
                current=_to_int(cur) or 0,
                bid_count=_to_int(row.get("bidCount") or row.get("bid_count")) or 0,
                url=row.get("detailUrl", "") or row.get("url", ""),
                keyword=row.get("searchKeyword", ""),
            ))
    return cands


def load_suruga_prices(path: Path, keyword: str = "") -> list[SurugaPrice]:
    d = json.loads(path.read_text(encoding="utf-8"))
    items = d.get("items", d) if isinstance(d, dict) else d
    prices: list[SurugaPrice] = []
    for it in items:
        if isinstance(it, dict):
            prices.append(SurugaPrice(
                title=it.get("name", ""),
                used=_to_int(it.get("used_price_jpy")),
                new=_to_int(it.get("new_price_jpy")),
                url=it.get("url", ""),
                keyword=keyword,
            ))
    return prices


def load_mercari_items(items: list[dict], keyword: str = "") -> list[SurugaPrice]:
    """Convert Mercari API items into resale-price references (used=asking price).

    Mercari is a true resale marketplace, so the asking `price` maps to the
    resale reference. Only on-sale genuine items (with a numeric id) count."""
    prices: list[SurugaPrice] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not it.get("id"):  # ad / non-genuine item
            continue
        price = _to_int(it.get("price"))
        if not price:
            continue
        prices.append(SurugaPrice(
            title=str(it.get("name", "")),
            used=price,
            new=None,
            url=f"https://jp.mercari.com/item/{it.get('id')}",
            keyword=keyword,
            source="mercari",
        ))
    return prices


def _to_int(v) -> int | None:
    if v is None:
        return None
    s = re.sub(r"[^\d]", "", str(v))
    return int(s) if s else None


def _price_of(used: int | None, new: int | None) -> int | None:
    """Resale reference price: prefer used price, else new."""
    res = used if used else new
    return int(res) if res else None


# ---------- analysis ----------

def analyze(cands: list[YahooCandidate], suruga: list[SurugaPrice],
            min_score: float, min_rate: float) -> list[ArbitrageHit]:
    """For each Yahoo sourcing candidate, find best-matching Suruga-ya resale
    price and compute margin. Self-use only — advises, never auto-trades.
    If Suruga prices carry a keyword tag, only candidates with the SAME
    keyword are matched (avoids cross-category false positives)."""
    # bucket suruga by keyword when tagged
    tagged = any(sp.keyword for sp in suruga)
    buckets: dict[str, list[SurugaPrice]] = {}
    if tagged:
        for sp in suruga:
            buckets.setdefault(_norm(sp.keyword), []).append(sp)
        all_suruga = list(suruga)
    else:
        all_suruga = list(suruga)

    hits: list[ArbitrageHit] = []
    for c in cands:
        pool = all_suruga
        if tagged:
            pool = buckets.get(_norm(c.keyword), [])
            if not pool:
                continue  # no resale prices for this keyword
        # Gather ALL matching references (same SKU, not accessory), scored.
        # We keep every reference so we can prefer real-market (mercari) comps
        # over higher-biased shop prices, and report a conservative low-bound.
        scored = []
        for sp in pool:
            if not _same_sku(c.title, sp.title):
                continue
            if _is_accessory(c.title, sp.title):
                continue
            score = _match_score(c.title, sp.title)
            if score >= min_score:
                scored.append((score, sp))
        if not scored:
            continue
        scored.sort(key=lambda s: s[0], reverse=True)

        # Distinct reference sets: mercari (real comps) vs shop (suruga/etc).
        mercari_refs = [(s, sp) for s, sp in scored if sp.source == "mercari"]
        shop_refs = [(s, sp) for s, sp in scored if sp.source != "mercari"]
        mercari_prices: list[int] = []
        for _, sp in mercari_refs:
            p = _price_of(sp.used, sp.new)
            if p:
                mercari_prices.append(p)
        mercari_prices.sort()
        shop_prices: list[int] = []
        for _, sp in shop_refs:
            p = _price_of(sp.used, sp.new)
            if p:
                shop_prices.append(p)
        shop_prices.sort()

        # Primary resale reference: prefer the best real-market (mercari) comp;
        # a low market ask is the most reliable "what actually sells for". Fall
        # back to the best shop price (higher bias) only when no mercari matches.
        source_type = "mercari"
        if mercari_refs:
            best_score, best_sp = mercari_refs[0]
            # Use the median mercari ask as the primary comp (robust vs one outlier).
            _median_price = int(median(mercari_prices)) if mercari_prices else None
            resale = _median_price if _median_price else _price_of(best_sp.used, best_sp.new)
            conservative_price = mercari_prices[0] if mercari_prices else (resale or 0)
            hits_count = len(mercari_refs)
            source_title, source_url = best_sp.title, best_sp.url
        elif shop_refs:
            best_score, best_sp = shop_refs[0]
            resale = _price_of(best_sp.used, best_sp.new)
            if not resale:
                continue
            source_type = "suruga"
            conservative_price = shop_prices[0] if shop_prices else resale
            hits_count = len(shop_refs)
            source_title, source_url = best_sp.title, best_sp.url
        else:
            continue
        if not resale:
            continue

        margin_yen = resale - c.buy_now
        margin_rate = margin_yen / c.buy_now if c.buy_now else 0.0
        cons_yen = conservative_price - c.buy_now
        conservative_rate = cons_yen / c.buy_now if c.buy_now else 0.0
        # Guard against accessory/false-scope artefacts: >= 1000% margin on a
        # ~zero sourcing cost almost always means a wrong-category match.
        if 0 < margin_rate < 10.0 and margin_rate >= min_rate:
            hits.append(ArbitrageHit(
                keyword=c.keyword,
                yahoo_title=c.title,
                yahoo_url=c.url,
                sourcing_cost=c.buy_now,
                resale_price=resale,
                margin_yen=margin_yen,
                margin_rate=round(margin_rate, 3),
                match_score=round(best_score, 3),
                source_title=source_title,
                source_url=source_url,
                source_type=source_type,
                conservative_price=conservative_price,
                conservative_rate=round(conservative_rate, 3),
                hits_count=hits_count,
            ))
    return hits


# ---------- CLI ----------

def main() -> None:
    p = argparse.ArgumentParser(description="Yahoo vs Suruga-ya margin analysis")
    p.add_argument("--yahoo", type=Path, required=True, help="Yahoo candidates CSV (from research_locally)")
    p.add_argument("--suruga", action="append", default=[],
                   help="Suruga-ya prices JSON. Use form KEYWORD=PATH to tag the "
                        "file with a keyword so it only matches same-keyword Yahoo "
                        "candidates. Repeatable. Untagged files match all candidates.")
    p.add_argument("--min-score", type=float, default=0.45)
    p.add_argument("--min-margin-rate", type=float, default=0.20)
    p.add_argument("--out", type=Path, default=None, help="Optional JSON output path")
    args = p.parse_args()

    cands = load_yahoo_candidates(args.yahoo, args.min_margin_rate)

    suruga: list[SurugaPrice] = []
    for s in args.suruga:
        if "=" in s:
            kw, path = s.split("=", 1)
            suruga.extend(load_suruga_prices(Path(path), keyword=kw.strip()))
        else:
            suruga.extend(load_suruga_prices(Path(s)))
    print(f"Yahoo sourcing candidates : {len(cands)}")
    print(f"Suruga-ya prices loaded   : {len(suruga)}")

    hits = analyze(cands, suruga, args.min_score, args.min_margin_rate)
    print(f"\n=== Arbitrage opportunities ({len(hits)} >= {args.min_margin_rate*100:.0f}% margin) ===")
    hits.sort(key=lambda h: h.margin_rate, reverse=True)
    for h in hits[:15]:
        print(f"  [+{h.margin_rate*100:.0f}%  +¥{h.margin_yen}] "
              f"{h.yahoo_title[:35]} | 仕入¥{h.sourcing_cost} → 売¥{h.resale_price}")

    if args.out:
        out = {"generatedAt": datetime.now(timezone.utc).isoformat(),
               "minScore": args.min_score, "minMarginRate": args.min_margin_rate,
               "hits": [h.__dict__ for h in hits],
               "count": len(hits)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote: {args.out}")


if __name__ == "__main__":
    main()
