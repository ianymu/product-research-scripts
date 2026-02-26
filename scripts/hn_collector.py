"""
V7 Pipeline — HackerNews Pain Point Collector via Algolia API
Replaces apify_hn.py. Free, no API key required.

Usage: python3 hn_collector.py [cycle_id]
"""
import os
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

ALGOLIA_BASE = "https://hn.algolia.com/api/v1/search"

SEARCH_QUERIES = [
    "Show HN", "Ask HN need", "frustrated with",
    "looking for tool", "built this because", "pain point"
]
HITS_PER_PAGE = 50
MAX_ITEMS = 300


def scrape_hn(cycle_id: int) -> dict:
    """Scrape HackerNews via Algolia API and write to Supabase."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

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


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting HN Algolia scrape for cycle {cycle_id}...")
    stats = scrape_hn(cycle_id)
    print(json.dumps(stats, indent=2))
