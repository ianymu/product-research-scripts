"""
V7 Pipeline — HackerNews Pain Point Collector via Algolia API
Replaces apify_hn.py. Free, no API key required.

Usage:
  python3 hn_collector.py [cycle_id]                     # daily cron (hardcoded queries)
  python3 hn_collector.py 2001 --queries-file q.json     # focused collection (custom queries)
"""
import os
import json
import sys
import time
import argparse
from datetime import datetime, timedelta, timezone

import httpx
import requests
from supabase import create_client, ClientOptions

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ALGOLIA_BASE = "https://hn.algolia.com/api/v1/search"

DEFAULT_SEARCH_QUERIES = [
    # Original 6 queries (kept)
    "Show HN", "Ask HN need", "frustrated with",
    "looking for tool", "built this because", "pain point",
    # New: toC / fundable / viral signals
    "consumer app", "went viral", "million users",
    "launched today", "raised funding", "acquired by",
]
HITS_PER_PAGE = 100
MAX_ITEMS = 1200

# Parse CLI args
parser = argparse.ArgumentParser(description="V7 HN Collector")
parser.add_argument("cycle_id", nargs="?", type=int, default=1)
parser.add_argument("--queries-file", type=str, default=None,
                    help="JSON file with custom queries for focused collection")
_args = parser.parse_args()

if _args.queries_file:
    with open(_args.queries_file) as _f:
        _custom = json.load(_f)
    SEARCH_QUERIES = _custom.get("queries", DEFAULT_SEARCH_QUERIES)
else:
    SEARCH_QUERIES = DEFAULT_SEARCH_QUERIES


def scrape_hn(cycle_id: int) -> dict:
    """Scrape HackerNews via Algolia API and write to Supabase."""
    sb = create_client(
        SUPABASE_URL, SUPABASE_KEY,
        options=ClientOptions(httpx_client=httpx.Client(verify=False))
    )

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    # 7 days ago as unix timestamp
    seven_days_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())

    for query in SEARCH_QUERIES:
        try:
            resp = requests.get(ALGOLIA_BASE, params={
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{seven_days_ago}",
                "hitsPerPage": HITS_PER_PAGE,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for hit in data.get("hits", []):
                object_id = hit.get("objectID", "")
                if not object_id or object_id in seen_ids:
                    continue
                seen_ids.add(object_id)
                results["total"] += 1

                # Build HN item URL
                source_url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"

                record = {
                    "cycle_id": cycle_id,
                    "source": "hackernews",
                    "source_url": source_url,
                    "source_id": object_id,
                    "author": hit.get("author", ""),
                    "title": hit.get("title", ""),
                    "content": (hit.get("story_text") or hit.get("title") or "")[:4000],
                    "raw_data": json.dumps(hit),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }

                try:
                    sb.table("pain_points").insert(record).execute()
                    results["written"] += 1
                except Exception as e:
                    if "23505" in str(e) or "duplicate" in str(e).lower():
                        results["duplicates"] += 1
                    else:
                        results["errors"] += 1
                        print(f"  Write error: {e}", file=sys.stderr)

        except requests.RequestException as e:
            print(f"  Algolia API error for query '{query}': {e}", file=sys.stderr)
            results["errors"] += 1

        # Polite delay between queries (Algolia has no strict rate limit but be nice)
        time.sleep(1)

    return results


MIN_TARGET = 300
MAX_RETRIES = 3
RETRY_DELAY = 300  # 5 minutes

# Focused mode: run once, no retry
if _args.queries_file:
    MIN_TARGET = 0
    MAX_RETRIES = 1


def main():
    cycle_id = _args.cycle_id
    print(f"Starting HN Algolia scrape for cycle {cycle_id}...")
    if _args.queries_file:
        print(f"  [FOCUSED] Using custom queries from {_args.queries_file}")

    result = {"written": 0}
    for attempt in range(1, MAX_RETRIES + 1):
        result = scrape_hn(cycle_id)
        if result["written"] >= MIN_TARGET:
            print(f"✅ HN: {result['written']} records (target: {MIN_TARGET})")
            break
        print(f"⚠️ Attempt {attempt}/{MAX_RETRIES}: only {result['written']}/{MIN_TARGET}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    else:
        print(f"❌ HN: {result['written']}/{MIN_TARGET} after {MAX_RETRIES} attempts")

    print(json.dumps(result, indent=2))
    print(f"RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    main()
