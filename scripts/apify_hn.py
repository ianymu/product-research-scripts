"""
V7 Pipeline — HackerNews Pain Point Scraper via Apify
"""
import os
import json
import sys
from datetime import datetime
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SEARCH_QUERIES = [
    "Show HN", "Ask HN need", "frustrated with",
    "looking for tool", "built this because", "pain point"
]
MAX_ITEMS = 300


def scrape_hn(cycle_id: int) -> dict:
    """Scrape HackerNews via Apify and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "errors": 0}

    run_input = {
        "startUrls": [
            {"url": "https://news.ycombinator.com/ask"},
            {"url": "https://news.ycombinator.com/show"},
            {"url": "https://news.ycombinator.com/newest"},
        ],
        "maxItems": MAX_ITEMS,
        "proxy": {"useApifyProxy": True},
    }

    try:
        run = client.actor("apify/hackernews-scraper").call(run_input=run_input)
        dataset = client.dataset(run["defaultDatasetId"])

        for item in dataset.iterate_items():
            results["total"] += 1
            record = {
                "cycle_id": cycle_id,
                "source": "hackernews",
                "source_url": item.get("url", f"https://news.ycombinator.com/item?id={item.get('id', '')}"),
                "source_id": str(item.get("id", "")),
                "author": item.get("by", ""),
                "title": item.get("title", ""),
                "content": (item.get("text", "") or item.get("title", ""))[:4000],
                "raw_data": json.dumps(item),
                "collected_at": datetime.utcnow().isoformat(),
            }

            try:
                sb.table("pain_points").insert(record).execute()
                results["written"] += 1
            except Exception as e:
                results["errors"] += 1
                print(f"  Write error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  HN scrape error: {e}", file=sys.stderr)

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting HN scrape for cycle {cycle_id}...")
    stats = scrape_hn(cycle_id)
    print(json.dumps(stats, indent=2))
