"""
V7 Pipeline — IndieHackers Web Scraper via Apify
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

START_URLS = [
    "https://www.indiehackers.com/posts?sort=trending",
    "https://www.indiehackers.com/posts?sort=newest",
]
MAX_ITEMS = 200


def scrape_indiehackers(cycle_id: int) -> dict:
    """Scrape IndieHackers via Apify Web Scraper."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "errors": 0}

    run_input = {
        "startUrls": [{"url": u} for u in START_URLS],
        "maxPagesPerCrawl": MAX_ITEMS,
        "proxy": {"useApifyProxy": True},
    }

    try:
        run = client.actor("apify/web-scraper").call(run_input=run_input)
        dataset = client.dataset(run["defaultDatasetId"])

        for item in dataset.iterate_items():
            results["total"] += 1
            record = {
                "cycle_id": cycle_id,
                "source": "indiehackers",
                "source_url": item.get("url", ""),
                "source_id": item.get("url", "").split("/")[-1] if item.get("url") else "",
                "author": item.get("author", ""),
                "title": item.get("title", ""),
                "content": (item.get("text", "") or item.get("body", "") or "")[:4000],
                "raw_data": json.dumps(item),
                "collected_at": datetime.utcnow().isoformat(),
            }

            try:
                sb.table("pain_points").insert(record).execute()
                results["written"] += 1
            except Exception as e:
                results["errors"] += 1
    except Exception as e:
        print(f"  IndieHackers scrape error: {e}", file=sys.stderr)

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting IndieHackers scrape for cycle {cycle_id}...")
    stats = scrape_indiehackers(cycle_id)
    print(json.dumps(stats, indent=2))
