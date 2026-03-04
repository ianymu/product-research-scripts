"""
V7 Pipeline — Reddit Pain Point Scraper via trudax/reddit-scraper-lite
Uses residential proxy (built-in) to avoid 403. Batches searches per subreddit.

Usage:
  python3 apify_reddit.py [cycle_id]                     # daily cron (hardcoded queries)
  python3 apify_reddit.py 2001 --queries-file q.json     # focused collection (custom queries)
"""
import os
import json
import sys
import time
import argparse
from datetime import datetime, timezone
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ACTOR_ID = "trudax/reddit-scraper-lite"

DEFAULT_SUBREDDITS = [
    # Original startup/tech subs
    "SaaS", "startups", "Entrepreneur", "indiehackers",
    # Consumer-facing subs (toC product discovery)
    "productivity", "personalfinance", "fitness", "selfimprovement",
    "apps", "technology", "Futurology", "ArtificialIntelligence",
]
DEFAULT_SEARCH_TERMS = [
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
MAX_POSTS_PER_SUB = 100
TIME_RANGE = "week"

# Parse CLI args
parser = argparse.ArgumentParser(description="V7 Reddit Scraper")
parser.add_argument("cycle_id", nargs="?", type=int, default=1)
parser.add_argument("--queries-file", type=str, default=None,
                    help="JSON file with custom subreddits/search_terms for focused collection")
_args = parser.parse_args()

if _args.queries_file:
    with open(_args.queries_file) as _f:
        _custom = json.load(_f)
    SUBREDDITS = _custom.get("subreddits", DEFAULT_SUBREDDITS)
    # Strip r/ prefix if LLM included it (e.g. "r/startups" → "startups")
    SUBREDDITS = [s.removeprefix("r/") for s in SUBREDDITS]
    SEARCH_TERMS = _custom.get("search_terms", DEFAULT_SEARCH_TERMS)
else:
    SUBREDDITS = DEFAULT_SUBREDDITS
    SEARCH_TERMS = DEFAULT_SEARCH_TERMS


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via trudax/reddit-scraper-lite and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    for subreddit in SUBREDDITS:
        # Batch all search terms for this subreddit into one actor run
        search_urls = []
        for term in SEARCH_TERMS:
            url = f"https://www.reddit.com/r/{subreddit}/search/?q={term.replace(' ', '+')}&sort=relevance&t={TIME_RANGE}"
            search_urls.append({"url": url})

        print(f"  r/{subreddit}: {len(search_urls)} search URLs in one run...")

        try:
            run = client.actor(ACTOR_ID).call(
                run_input={
                    "startUrls": search_urls,
                    "maxItems": MAX_POSTS_PER_SUB,
                    "maxPostCount": MAX_POSTS_PER_SUB,
                    "skipComments": True,
                    "sort": "relevance",
                    "time": TIME_RANGE,
                    "proxy": {
                        "useApifyProxy": True,
                        "apifyProxyGroups": ["RESIDENTIAL"],
                    },
                },
                timeout_secs=300,
            )
            dataset = client.dataset(run["defaultDatasetId"])
            sub_count = 0

            for item in dataset.iterate_items():
                post_id = item.get("parsedId", "") or item.get("id", "")
                if not post_id or post_id in seen_ids:
                    results["duplicates"] += 1
                    continue
                seen_ids.add(post_id)
                results["total"] += 1

                record = {
                    "cycle_id": cycle_id,
                    "source": "reddit",
                    "source_url": item.get("url", ""),
                    "source_id": str(post_id),
                    "author": item.get("username", "") or item.get("author", ""),
                    "title": item.get("title", ""),
                    "content": (item.get("body", "") or item.get("title", ""))[:4000],
                    "raw_data": json.dumps(item),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }

                try:
                    sb.table("pain_points").insert(record).execute()
                    results["written"] += 1
                    sub_count += 1
                except Exception as e:
                    if "23505" in str(e) or "duplicate" in str(e).lower():
                        results["duplicates"] += 1
                    else:
                        results["errors"] += 1
                        print(f"  Write error: {e}", file=sys.stderr)

            print(f"  r/{subreddit}: {sub_count} new posts written")

        except Exception as e:
            print(f"  Scrape error for r/{subreddit}: {e}", file=sys.stderr)
            results["errors"] += 1

    return results


MIN_TARGET = 300
MAX_RETRIES = 2
RETRY_DELAY = 120  # 2 minutes


def main():
    cycle_id = _args.cycle_id
    print(f"Starting Reddit scrape (lite + residential proxy) for cycle {cycle_id}...")
    print(f"  {len(SUBREDDITS)} subs, {len(SEARCH_TERMS)} terms, batched = {len(SUBREDDITS)} actor runs")
    if _args.queries_file:
        print(f"  [FOCUSED] Using custom queries from {_args.queries_file}")

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
