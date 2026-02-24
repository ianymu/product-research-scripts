"""
V7 Pipeline — Reddit Pain Point Scraper via Apify
Scrapes subreddits for SaaS/startup pain points.
"""
import os
import json
import sys
from datetime import datetime
from apify_client import ApifyClient
from supabase import create_client

# Config from environment
APIFY_API_KEY = os.environ["APIFY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SUBREDDITS = [
    "SaaS", "startups", "Entrepreneur", "smallbusiness",
    "microsaas", "indiehackers", "webdev", "programming"
]
SEARCH_TERMS = [
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "looking for", "alternative to", "struggling with"
]
MAX_ITEMS = 500
TIME_RANGE = "week"


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via Apify and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "errors": 0}

    for subreddit in SUBREDDITS:
        try:
            run_input = {
                "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/"}],
                "searchTerms": SEARCH_TERMS,
                "maxItems": MAX_ITEMS // len(SUBREDDITS),
                "sort": "relevance",
                "time": TIME_RANGE,
                "proxy": {"useApifyProxy": True},
            }

            run = client.actor("apify/reddit-scraper").call(run_input=run_input)
            dataset = client.dataset(run["defaultDatasetId"])

            for item in dataset.iterate_items():
                results["total"] += 1
                record = {
                    "cycle_id": cycle_id,
                    "source": "reddit",
                    "source_url": item.get("url", ""),
                    "source_id": item.get("id", ""),
                    "author": item.get("username", ""),
                    "title": item.get("title", ""),
                    "content": (item.get("body", "") or item.get("title", ""))[:4000],
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
            print(f"  Scrape error for r/{subreddit}: {e}", file=sys.stderr)
            results["errors"] += 1

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit scrape for cycle {cycle_id}...")
    stats = scrape_reddit(cycle_id)
    print(json.dumps(stats, indent=2))
