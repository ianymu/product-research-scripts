"""
V7 Pipeline — X/Twitter Scraper via Apify
Adapted from MoltBot X Scraper. Uses Apify API instead of Playwright.
Reuses tier0/filtered account config from x_accounts.json.
"""
import os
import json
import sys
from datetime import datetime, timedelta
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

MAX_AGE_DAYS = 15

# Account tiers (from MoltBot x_accounts.json)
TIER0_ACCOUNTS = [
    # Original 16 indie makers + AI labs
    "levelsio", "dannypostma", "marclouvion", "mckaywrigley",
    "tibo_maker", "OpenAI", "AnthropicAI", "GoogleDeepMind",
    "xai", "MistralAI", "perplexity_ai", "karpathy",
    "sama", "ylecun", "drjimfan", "AndrewYNg",
    # New: VC / product discovery (gold for toC/fundable signals)
    "ProductHunt", "ycombinator", "paulg", "naval", "garrytan",
]

FILTERED_ACCOUNTS = [
    # Original 5
    "bcherny", "_catwu", "cursor_ai", "_akhaliq", "rowancheung",
    # New: product/growth experts
    "lennysan", "gregisenberg", "Jason",
]

FILTER_RULES = {
    "min_likes_within_6h": 100,
    "min_retweets_within_6h": 20,
    "min_likes_after_6h": 500,
    "min_retweets_after_6h": 50,
}


def passes_filter(tweet: dict) -> bool:
    """Check if tweet passes engagement filter."""
    likes = tweet.get("likeCount", 0) or 0
    retweets = tweet.get("retweetCount", 0) or 0
    created = tweet.get("createdAt", "")

    try:
        tweet_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_hours = (datetime.now(tweet_time.tzinfo) - tweet_time).total_seconds() / 3600
    except Exception:
        age_hours = 24

    if age_hours <= 6:
        return likes >= FILTER_RULES["min_likes_within_6h"] or retweets >= FILTER_RULES["min_retweets_within_6h"]
    return likes >= FILTER_RULES["min_likes_after_6h"] or retweets >= FILTER_RULES["min_retweets_after_6h"]


def scrape_x(cycle_id: int) -> dict:
    """Scrape X/Twitter via Apify and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)

    results = {"total": 0, "written": 0, "errors": 0, "filtered_out": 0}

    all_accounts = [(a, False) for a in TIER0_ACCOUNTS] + [(a, True) for a in FILTERED_ACCOUNTS]

    for username, apply_filter in all_accounts:
        try:
            run_input = {
                "handle": [username],
                "maxTweets": 30,
                "proxy": {"useApifyProxy": True},
            }

            run = client.actor("apify/twitter-scraper").call(run_input=run_input)
            dataset = client.dataset(run["defaultDatasetId"])

            for item in dataset.iterate_items():
                results["total"] += 1

                # Age filter
                created = item.get("createdAt", "")
                try:
                    tweet_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if tweet_time.replace(tzinfo=None) < cutoff:
                        results["filtered_out"] += 1
                        continue
                except Exception:
                    pass

                # Engagement filter for filtered accounts
                if apply_filter and not passes_filter(item):
                    results["filtered_out"] += 1
                    continue

                record = {
                    "cycle_id": cycle_id,
                    "source": "twitter",
                    "source_url": item.get("url", f"https://x.com/{username}/status/{item.get('id', '')}"),
                    "source_id": str(item.get("id", "")),
                    "author": username,
                    "title": (item.get("text", "")[:50] + "...") if len(item.get("text", "")) > 50 else item.get("text", ""),
                    "content": (item.get("text", "") or "")[:4000],
                    "raw_data": json.dumps(item),
                    "collected_at": datetime.utcnow().isoformat(),
                }

                try:
                    sb.table("pain_points").insert(record).execute()
                    results["written"] += 1
                except Exception as e:
                    if "23505" in str(e) or "duplicate" in str(e).lower():
                        results.setdefault("duplicates", 0)
                        results["duplicates"] += 1
                    else:
                        results["errors"] += 1

        except Exception as e:
            print(f"  X scrape error for @{username}: {e}", file=sys.stderr)
            results["errors"] += 1

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting X/Twitter scrape for cycle {cycle_id}...")
    stats = scrape_x(cycle_id)
    print(json.dumps(stats, indent=2))
