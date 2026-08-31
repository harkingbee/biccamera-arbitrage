#!/usr/bin/env python3
"""Integrated resale pipeline (local, Japan IP).

One command: for each watchlist entry it
  1) scrapes Yahoo Auctions for sourcing candidates (buy-now priced),
  2) scrapes Suruga-ya for the resale side (used / new prices),
  3) runs cross-market margin analysis and writes a combined daily report.

Since Yahoo and the sibling shop keyword search usually need different terms,
each watchlist entry may carry an optional "surugaKeyword" to scope the
Suruga-ya search precisely (e.g. the model number). If absent, the general
keyword is reused.

Usage:
    python3 -m research.integrated_pipeline --config research/watchlist.json \
        --out data/research/daily --min-margin-rate 0.20
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# allow import from this project and from the sibling suruga-scraper
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

SURUGA_SCRAPER_DIR = Path("/mnt/d/Project2/suruga-scraper")


def _load_entries(config: Path) -> list[dict]:
    data = json.loads(config.read_text(encoding="utf-8"))
    if isinstance(data, list) and data and isinstance(data[0], str):
        return [{"keyword": k} for k in data]
    return data


def _scrape_yahoo(entries: list[dict], max_items: int, max_pages: int) -> list[dict]:
    from research.research_locally import search_keyword
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    all_items: list[dict] = []
    async_import = __import__("asyncio")

    async def _run():
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            for e in entries:
                kw = e["keyword"]
                print(f"[yahoo] searching '{kw}'", flush=True)
                items = await search_keyword(client, kw, max_pages, max_items)
                for it in items:
                    it["searchKeyword"] = kw
                all_items.extend(items)
    async_import.run(_run())
    return all_items


def _scrape_suruga(entry: dict, out_dir: Path) -> Path | None:
    """Scrape Suruga-ya for one entry. Returns the output JSON path, or None.

    Note: suruga scraper writes to its OWN data/ dir (DATA_DIR / output), so we
    hand it a relative filename and read it back from there."""
    kw = entry.get("surugaKeyword") or entry["keyword"]
    # scraper joins its DATA_DIR; a bare filename keeps it under its own data/
    rela_name = f"research_out_{_safe(kw)}.json"
    cmd = [sys.executable, "src/__main__.py", kw, "--pages", "2",
           "--in-stock", "--output", rela_name]
    try:
        r = subprocess.run(
            cmd, cwd=SURUGA_SCRAPER_DIR, capture_output=True, text=True, timeout=180
        )
        produced = SURUGA_SCRAPER_DIR / "data" / rela_name
        if produced.exists() and produced.stat().st_size > 0:
            return produced
        print(f"[suruga] '{kw}' -> no file (rc={r.returncode})", flush=True)
        err = (r.stderr or r.stdout or "").strip().splitlines()
        if err:
            print("  ", err[-1], flush=True)
        return None
    except subprocess.TimeoutExpired:
        print(f"[suruga] '{kw}' timed out", flush=True)
        return None


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_")[:40] or "kw"


# Apify Mercari actor (published, DC-IP direct works; proxy must be OFF).
MERCARI_ACTOR_ID = "whSePszWpMtfeLYBp"
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")


def _fetch_mercari(keyword: str, max_items: int = 12) -> list[dict]:
    """Fetch resale asking prices from Mercari via the Apify actor's sync API.
    Returns raw API items. Uses DC IP (no proxy) — auto proxy fails on Mercari."""
    if not APIFY_TOKEN:
        print("[mercari] APIFY_TOKEN not set -> skipped", flush=True)
        return []
    body = json.dumps({
        "searchKeyword": keyword,
        "maxItems": int(max_items),
        "maxPages": 1,
        "proxyConfiguration": {"useApifyProxy": False},
    }).encode()
    url = (f"https://api.apify.com/v2/acts/{MERCARI_ACTOR_ID}/"
           f"run-sync-get-dataset-items?token={APIFY_TOKEN}&timeout=180")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            items = json.loads(r.read().decode())
        print(f"[mercari] '{keyword}' -> {len(items)} items", flush=True)
        return items
    except Exception as exc:
        print(f"[mercari] '{keyword}' failed: {exc}", flush=True)
        return []


def main() -> None:
    p = argparse.ArgumentParser(description="Integrated Yahoo+Suruga resale pipeline")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--out", type=Path, default=_ROOT / "data" / "research" / "daily")
    p.add_argument("--max-items", type=int, default=60)
    p.add_argument("--max-pages", type=int, default=2)
    p.add_argument("--min-margin-rate", type=float, default=0.20)
    p.add_argument("--workers", type=int, default=2, help="Paralleel Suruga-ya scrapers")
    p.add_argument("--mercari", action="store_true",
                   help="Also pull Mercari resale prices for entries with \"mercari\": true in the watchlist")
    p.add_argument("--mercari-max", type=int, default=10, help="Max Mercari items per keyword")
    p.add_argument("--mercari-workers", type=int, default=4,
                   help="Parallel Mercari sync-API calls (each ~60s)")
    args = p.parse_args()

    name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = args.out / name
    run_dir.mkdir(parents=True, exist_ok=True)

    entries = _load_entries(args.config)
    print(f"[pipeline] {len(entries)} watchlist entries")

    # 1. Yahoo sourcing
    yahoo_items = _scrape_yahoo(entries, args.max_items, args.max_pages)
    cands_csv = run_dir / "yahoo_candidates.csv"
    import csv as _csv
    fields = ["searchKeyword", "itemId", "title", "currentPrice", "buyNowPrice",
              "bidCount", "timeLeft", "detailUrl", "scrapedAt"]
    with cands_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(yahoo_items)
    print(f"[pipeline] Yahoo: {len(yahoo_items)} items -> {cands_csv.name}")

    # 2. Suruga-ya resale per entry (parallel but modest)
    from research.margin_analyzer import (load_suruga_prices, load_yahoo_candidates,
                                          analyze, load_mercari_items)

    suruga_files: list[tuple[str, Path]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut = {ex.submit(_scrape_suruga, e, run_dir): e for e in entries}
        for f in as_completed(fut):
            e = fut[f]
            out = f.result()
            if out is not None:
                suruga_files.append((e["keyword"], out))
    print(f"[pipeline] Suruga-ya: {len(suruga_files)} files")

    # 2b. Mercari resale asking prices (Apify sync API, optional per-entry
    #     "mercari": true — it costs ~$0.02/run so default is off). Fetched in
    #     parallel because each sync call takes ~60s (Mercari is browser-heavy).
    mercari_entries = [e for e in entries if e.get("mercari", False)]
    mercari_items: list[tuple[str, list[dict]]] = []
    if mercari_entries and args.mercari:
        def _fetch_one(e):
            kw = e.get("mercariKeyword") or e["keyword"]
            items = _fetch_mercari(kw, max_items=args.mercari_max)
            return (e["keyword"], items) if items else None
        with ThreadPoolExecutor(max_workers=args.mercari_workers) as ex:
            for res in ex.map(_fetch_one, mercari_entries):
                if res is not None:
                    mercari_items.append(res)
        print(f"[pipeline] Mercari: {len(mercari_items)} keywords")

    # 3. Margin analysis (keyword-scoped)
    cands = load_yahoo_candidates(cands_csv, args.min_margin_rate)
    all_suruga = []
    for kw, path in suruga_files:
        all_suruga.extend(load_suruga_prices(path, keyword=kw))
    for kw, items in mercari_items:
        all_suruga.extend(load_mercari_items(items, keyword=kw))
    hits = analyze(cands, all_suruga, min_score=0.35, min_rate=args.min_margin_rate)
    hits.sort(key=lambda h: h.margin_rate, reverse=True)

    print(f"\n=== Margin opportunities ({len(hits)} >= {args.min_margin_rate*100:.0f}%) ===")
    print(f"  (suruga={len(suruga_files)}, mercari={len(mercari_items)})")
    for h in hits[:15]:
        print(f"  [+{h.margin_rate*100:.0f}% +¥{h.margin_yen}] {h.yahoo_title[:38]} "
              f"| ¥{h.sourcing_cost}→¥{h.resale_price}")

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "watchlist": entries,
        "counts": {"yahoo": len(yahoo_items), "surugaFiles": len(suruga_files),
                   "mercari": len(mercari_items), "hits": len(hits)},
        "hits": [h.__dict__ for h in hits],
    }
    out_file = run_dir / "report.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[pipeline] report -> {out_file}")


if __name__ == "__main__":
    main()
