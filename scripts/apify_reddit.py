"""
V7 Pipeline — Reddit Pain Point Scraper via trudax/reddit-scraper (paid Actor)
Scrapes subreddits for toC/startup pain points.

Usage: python3 apify_reddit.py [cycle_id]
"""
import os
import json
import sys
import time
from datetime import datetime, timezone
from apify_client import ApifyClient
try:
    from supabase import create_client
    USE_LITE = False
except ImportError:
    from supabase_lite import SupabaseLite, DuplicateError
    USE_LITE = True

APIFY_API_KEY = os.environ["APIFY_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ACTOR_ID = "trudax/reddit-scraper"

SUBREDDITS = [
    # Original startup/tech subs
    "SaaS", "startups", "Entrepreneur", "indiehackers",
    # Consumer-facing subs (toC product discovery)
    "productivity", "personalfinance", "fitness", "selfimprovement",
    "apps", "technology", "Futurology", "ArtificialIntelligence",
]
SEARCH_TERMS = [
    # Original 9 keywords (kept)
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "looking for", "alternative to", "struggling with",
    # New: direct demand signals
    "someone should build", "why isn't there",
    "I'd pay for", "can't believe there's no",
    # New: virality / fundable signals
    "switched from", "shut up and take my money",
    "addicted to", "everyone is using", "went viral",
    "changed my life", "million users",
]
MAX_ITEMS_PER_SEARCH = 15
TIME_RANGE = "week"


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via trudax/reddit-scraper and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = SupabaseLite(SUPABASE_URL, SUPABASE_KEY) if USE_LITE else create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    for subreddit in SUBREDDITS:
        for term in SEARCH_TERMS:
            search_url = f"https://www.reddit.com/r/{subreddit}/search/?q={term.replace(' ', '+')}&sort=relevance&t={TIME_RANGE}"
            try:
                run = client.actor(ACTOR_ID).call(
                    run_input={
                        "startUrls": [{"url": search_url}],
                        "maxItems": MAX_ITEMS_PER_SEARCH,
                        "proxy": {"useApifyProxy": True},
                        "skipComments": True,
                    },
                    timeout_secs=120,
                )
                dataset = client.dataset(run["defaultDatasetId"])

                for item in dataset.iterate_items():
                    post_id = item.get("parsedId", "") or item.get("id", "")
                    if not post_id or post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)
                    results["total"] += 1

                    record = {
                        "cycle_id": cycle_id,
                        "source": "reddit",
                        "source_url": item.get("url", ""),
                        "source_id": str(post_id),
                        "author": item.get("username", ""),
                        "title": item.get("title", ""),
                        "content": (item.get("body", "") or item.get("title", ""))[:4000],
                        "raw_data": json.dumps(item),
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                    }

                    try:
                        if USE_LITE:
                            sb.insert("pain_points", record)
                        else:
                            sb.table("pain_points").insert(record).execute()
                        results["written"] += 1
                    except Exception as e:
                        if "23505" in str(e) or "duplicate" in str(e).lower() or "DuplicateError" in type(e).__name__:
                            results["duplicates"] += 1
                        else:
                            results["errors"] += 1
                            print(f"  Write error: {e}", file=sys.stderr)

            except Exception as e:
                print(f"  Scrape error for r/{subreddit} [{term}]: {e}", file=sys.stderr)
                results["errors"] += 1

    return results


MIN_TARGET = 500
MAX_RETRIES = 3
RETRY_DELAY = 300  # 5 minutes


def main():
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit scrape (trudax) for cycle {cycle_id}...")
    print(f"  {len(SUBREDDITS)} subs x {len(SEARCH_TERMS)} terms = {len(SUBREDDITS) * len(SEARCH_TERMS)} searches")

    result = {"written": 0}
    for attempt in range(1, MAX_RETRIES + 1):
        result = scrape_reddit(cycle_id)
        if result["written"] >= MIN_TARGET:
            print(f"✅ Reddit: {result['written']} records (target: {MIN_TARGET})")
            break
        print(f"⚠️ Attempt {attempt}/{MAX_RETRIES}: only {result['written']}/{MIN_TARGET}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    else:
        print(f"❌ Reddit: {result['written']}/{MIN_TARGET} after {MAX_RETRIES} attempts")

    print(json.dumps(result, indent=2))
    # Machine-readable result line for DataCollector parsing
    print(f"RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    main()
