"""
V7 Pipeline — X/Twitter Scraper via Apify (apidojo/tweet-scraper)
Uses searchTerms for both keyword search AND account monitoring (via from:username).

Usage:
  python3 apify_x.py [cycle_id]                     # daily cron (hardcoded queries)
  python3 apify_x.py 2001 --queries-file q.json     # focused collection (custom queries)
"""
import os
import json
import sys
import time
import argparse
from datetime import datetime, timedelta, timezone
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ACTOR_ID = "apidojo/tweet-scraper"
MAX_AGE_DAYS = 15
MAX_TWEETS_PER_SEARCH = 30

# --- Account monitoring via "from:username" search ---
# Removed noise accounts (politics/philosophy/academic): ylecun, Jason, garrytar,
# paulg, karpathy, drjimfan, AndrewYNg, sama, naval
TIER0_ACCOUNTS = [
    # Indie makers (core product signal)
    "levelsio", "dannypostma", "marclouvion", "mckaywrigley", "tibo_maker",
    # AI product companies
    "OpenAI", "AnthropicAI", "GoogleDeepMind", "xai", "MistralAI", "perplexity_ai",
    # Product discovery
    "ProductHunt", "ycombinator",
    # Product/growth
    "gregisenberg",
]

FILTERED_ACCOUNTS = [
    "bcherny", "_catwu", "cursor_ai", "_akhaliq", "rowancheung",
    "lennysan",
]

FILTER_RULES = {
    "min_likes_within_6h": 100,
    "min_retweets_within_6h": 20,
    "min_likes_after_6h": 500,
    "min_retweets_after_6h": 50,
}

# --- Keyword searches for toC/fundable product signals ---
DEFAULT_KEYWORD_SEARCHES = [
    '"I wish there was" AND ("app" OR "tool" OR "software" OR "product")',
    '"someone should build" AND ("app" OR "tool" OR "software" OR "product")',
    '"paying for" AND ("app" OR "tool" OR "software") AND ("frustrating" OR "broken" OR "terrible")',
    '"switched from" AND ("app" OR "tool" OR "software") AND ("better" OR "alternative")',
    '"launched" AND ("app" OR "product" OR "tool") AND ("users" OR "signup" OR "waitlist")',
]

# Parse CLI args
parser = argparse.ArgumentParser(description="V7 X/Twitter Scraper")
parser.add_argument("cycle_id", nargs="?", type=int, default=1)
parser.add_argument("--queries-file", type=str, default=None,
                    help="JSON file with custom keyword_searches for focused collection")
_args = parser.parse_args()

if _args.queries_file:
    with open(_args.queries_file) as _f:
        _custom = json.load(_f)
    KEYWORD_SEARCHES = _custom.get("keyword_searches", DEFAULT_KEYWORD_SEARCHES)
else:
    KEYWORD_SEARCHES = DEFAULT_KEYWORD_SEARCHES


def passes_filter(tweet: dict) -> bool:
    """Check if tweet passes engagement filter."""
    likes = tweet.get("likeCount", 0) or 0
    retweets = tweet.get("retweetCount", 0) or 0
    created = tweet.get("createdAt", "")
    try:
        tweet_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - tweet_time).total_seconds() / 3600
    except Exception:
        age_hours = 24
    if age_hours <= 6:
        return likes >= FILTER_RULES["min_likes_within_6h"] or retweets >= FILTER_RULES["min_retweets_within_6h"]
    return likes >= FILTER_RULES["min_likes_after_6h"] or retweets >= FILTER_RULES["min_retweets_after_6h"]


def run_search(client, query, max_tweets=MAX_TWEETS_PER_SEARCH):
    """Run a single search via apidojo/tweet-scraper."""
    run = client.actor(ACTOR_ID).call(
        run_input={"searchTerms": [query], "maxTweets": max_tweets, "proxy": {"useApifyProxy": True}},
        timeout_secs=300,
    )
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


def scrape_x(cycle_id: int) -> dict:
    """Scrape X/Twitter via Apify and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0, "filtered_out": 0}
    seen_ids = set()

    def process_tweet(item, source_username="", apply_filter=False):
        results["total"] += 1
        source_id = str(item.get("id", ""))
        if not source_id or source_id in seen_ids:
            return
        seen_ids.add(source_id)

        # Age filter (Twitter format: "Mon Mar 17 22:14:00 +0000 2025")
        created = item.get("createdAt", "")
        try:
            tweet_time = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
            if tweet_time < cutoff:
                results["filtered_out"] += 1
                return
        except Exception:
            pass

        # Engagement filter for filtered accounts
        if apply_filter and not passes_filter(item):
            results["filtered_out"] += 1
            return

        author = source_username or item.get("author", {}).get("userName", "") or "unknown"
        text = item.get("text", "") or item.get("full_text", "") or ""

        record = {
            "cycle_id": cycle_id,
            "source": "twitter",
            "source_url": item.get("url", "https://x.com/i/status/" + source_id),
            "source_id": source_id,
            "author": author,
            "title": (text[:50] + "...") if len(text) > 50 else text,
            "content": text[:4000],
            "raw_data": json.dumps(item),
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

    # Part 1: Account monitoring via "from:username" (batch 5 per query)
    all_accounts = [(a, False) for a in TIER0_ACCOUNTS] + [(a, True) for a in FILTERED_ACCOUNTS]
    batch_size = 5
    for i in range(0, len(all_accounts), batch_size):
        batch = all_accounts[i:i + batch_size]
        query = " OR ".join("from:" + a[0] for a in batch)
        apply_filter = any(a[1] for a in batch)
        try:
            print(f"  Accounts batch {i // batch_size + 1}: {query[:60]}...")
            items = run_search(client, query, max_tweets=batch_size * MAX_TWEETS_PER_SEARCH)
            for item in items:
                author = item.get("author", {}).get("userName", "") or ""
                is_filtered = any(a[0].lower() == author.lower() and a[1] for a in batch)
                process_tweet(item, author, is_filtered)
        except Exception as e:
            print(f"  Account batch error: {e}", file=sys.stderr)
            results["errors"] += 1
        time.sleep(3)

    # Part 2: Keyword searches
    for i, query in enumerate(KEYWORD_SEARCHES):
        try:
            print(f"  Keyword {i + 1}/{len(KEYWORD_SEARCHES)}: {query[:50]}...")
            items = run_search(client, query)
            for item in items:
                process_tweet(item)
        except Exception as e:
            print(f"  Keyword search error: {e}", file=sys.stderr)
            results["errors"] += 1
        time.sleep(3)

    return results


MIN_TARGET = 500
MAX_RETRIES = 3
RETRY_DELAY = 300  # 5 minutes


def main():
    cycle_id = _args.cycle_id
    print(f"Starting X/Twitter scrape for cycle {cycle_id}...")
    if _args.queries_file:
        print(f"  [FOCUSED] Using custom queries from {_args.queries_file}")

    result = {"written": 0}
    for attempt in range(1, MAX_RETRIES + 1):
        result = scrape_x(cycle_id)
        if result["written"] >= MIN_TARGET:
            print(f"✅ X/Twitter: {result['written']} records (target: {MIN_TARGET})")
            break
        print(f"⚠️ Attempt {attempt}/{MAX_RETRIES}: only {result['written']}/{MIN_TARGET}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    else:
        print(f"❌ X/Twitter: {result['written']}/{MIN_TARGET} after {MAX_RETRIES} attempts")

    print(json.dumps(result, indent=2))
    print(f"RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    main()
