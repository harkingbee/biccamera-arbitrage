#!/usr/bin/env python3
"""Yahoo Auctions local resale research.

Runs the scraper locally (Japan IP) over a watchlist of keywords and writes a
daily CSV/JSON to a data/ folder. Flags:
  - high-demand items  : bidCount >= 5  (need signal)
  - sourcing candidates: buyNowPrice <= sourcingMaxYen and low current price
This is the foundation for cross-market margin analysis (compare against
Mercari / Suruga-ya buy-now prices separately).

Usage:
    python3 -m research.research_locally --keywords "SONY α7 IV" --keywords "POKEMON カード"
    python3 -m research.research_locally --config research/watchlist.json --max-items 100 --max-pages 3
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.yahoo_auctions import fetch_page, list_page, parse_page

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "research"


def load_watchlist(path: Path) -> list[dict]:
    """Load watchlist: [{keyword, sourcingMaxYen}]. Accepts a bare string list too."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list) and data and isinstance(data[0], str):
        return [{"keyword": k, "sourcingMaxYen": None} for k in data]
    return data


async def search_keyword(client: httpx.AsyncClient, keyword: str, max_pages: int,
                         max_items: int) -> list[dict]:
    base = (
        "https://auctions.yahoo.co.jp/search/search?"
        f"p={quote(keyword)}&auccat=&tab_ex=commerce"
    )
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        url = list_page(base, page)
        try:
            html = await fetch_page(client, url)
        except Exception:
            break
        batch = parse_page(html)
        for it in batch:
            it["searchKeyword"] = keyword
            items.append(it)
            if len(items) >= max_items:
                break
        if len(items) >= max_items or not batch:
            break
    return items


def flag_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (high_demand, sourcing_candidates)."""
    high_demand = [it for it in items if (it.get("bidCount") or 0) >= 5]
    sourcing = [it for it in items if it.get("buyNowPrice")]
    return high_demand, sourcing


async def run(keywords: list[str], max_items: int, max_pages: int,
              sourcing_max: int | None) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    all_items: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for kw in keywords:
            print(f"[INFO] Searching '{kw}' ...")
            items = await search_keyword(client, kw, max_pages, max_items)
            all_items.extend(items)
            print(f"  -> {len(items)} items")
        # gentle delay between keywords is inherent (sequential fetch)

    for it in all_items:
        it.setdefault("scrapedAt", now)

    report = {
        "generatedAt": now,
        "keywords": keywords,
        "totalItems": len(all_items),
    }

    # Need-signal (high bid) per keyword
    report["highDemand"] = [
        it for it in all_items if (it.get("bidCount") or 0) >= 5
    ]
    # Sourcing candidates (has buy-now price; optionally under cap)
    cands = [it for it in all_items if it.get("buyNowPrice")]
    if sourcing_max is not None:
        cands = [it for it in cands if (it.get("buyNowPrice") or 0) <= sourcing_max]
    report["sourcingCandidates"] = cands
    report["allItems"] = all_items
    return report


def write_outputs(report: dict, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    written = []

    json_path = out_dir / f"research_{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(json_path))

    # CSV of all items for spreadsheet analysis
    if report["allItems"]:
        csv_path = out_dir / f"items_{stamp}.csv"
        fields = ["searchKeyword", "itemId", "title", "currentPrice",
                  "buyNowPrice", "bidCount", "timeLeft", "postage",
                  "detailUrl", "scrapedAt"]
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(report["allItems"])
        written.append(str(csv_path))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Yahoo Auctions local resale research")
    parser.add_argument("--keywords", action="append", help="A keyword to search (repeatable)")
    parser.add_argument("--config", type=Path, help="JSON watchlist file (list or [{keyword,sourcingMaxYen}])")
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--sourcing-max", type=int, default=5000,
                        help="Max buy-now price in JPY to count as sourcing candidate (default 5000)")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    if args.config:
        watch = load_watchlist(args.config)
        keywords = [str(w["keyword"]) for w in watch]
        smax = args.sourcing_max
    else:
        keywords = args.keywords or []
        smax = args.sourcing_max

    if not keywords:
        parser.error("Provide --keywords or --config watchlist")
        return

    report = asyncio.run(run(keywords, args.max_items, args.max_pages, smax))
    written = write_outputs(report, args.out)
    print(f"\n=== Summary ===")
    print(f"Total items : {report['totalItems']}")
    print(f"High demand : {len(report['highDemand'])} (bidCount>=5)")
    print(f"Sourcing    : {len(report['sourcingCandidates'])} candidates")
    for p in written:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
