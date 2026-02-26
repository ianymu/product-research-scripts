"""
V7 Pipeline — Reddit Pain Point Collector via PullPush.io API
Replaces apify_reddit.py. Free, no API key or registration needed.
Works from cloud servers (EC2/AWS) — Reddit's own JSON API blocks datacenter IPs.

PullPush.io = Reddit data archive, free API, no auth required.
Endpoint: https://api.pullpush.io/reddit/search/submission/

Usage: python3 reddit_collector.py [cycle_id]
"""
import os
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from supabase import create_client

# Supabase (no Reddit credentials needed)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"

SUBREDDITS = [
    "SaaS", "startups", "Entrepreneur", "smallbusiness",
    "microsaas", "indiehackers", "webdev", "programming"
]
SEARCH_TERMS = [
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "looking for", "alternative to", "struggling with"
]
# PullPush returns up to 100 per request
LIMIT_PER_REQUEST = 100
# Polite delay between requests
REQUEST_DELAY = 2
USER_AGENT = "v7-pipeline:1.0 (pain point research)"


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via PullPush.io API and write to Supabase."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0, "requests": 0}
    seen_ids = set()

    # 7 days ago as unix timestamp
    seven_days_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())

    for subreddit_name in SUBREDDITS:
        sub_count = 0

        for term in SEARCH_TERMS:
            try:
                resp = session.get(
                    PULLPUSH_BASE,
                    params={
                        "subreddit": subreddit_name,
                        "q": term,
                        "after": seven_days_ago,
                        "size": LIMIT_PER_REQUEST,
                        "sort": "desc",
                    },
                    timeout=30,
                )
                results["requests"] += 1

                if resp.status_code == 429:
                    print(f"  Rate limited, waiting 60s...", file=sys.stderr)
                    time.sleep(60)
                    continue

                resp.raise_for_status()
                data = resp.json()

                posts = data.get("data", [])
                for post in posts:
                    post_id = post.get("id", "")
                    if not post_id or post_id in seen_ids:
                        continue

                    seen_ids.add(post_id)
                    results["total"] += 1
                    sub_count += 1

                    content = post.get("selftext") or post.get("title", "")
                    author = post.get("author", "")
                    permalink = post.get("permalink", "")

                    raw = {
                        "id": post_id,
                        "subreddit": subreddit_name,
                        "title": post.get("title", ""),
                        "selftext": (post.get("selftext") or "")[:4000],
                        "author": author,
                        "score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "created_utc": post.get("created_utc", 0),
                        "url": post.get("url", ""),
                        "permalink": permalink,
                    }

                    record = {
                        "cycle_id": cycle_id,
                        "source": "reddit",
                        "source_url": f"https://www.reddit.com{permalink}" if permalink else "",
                        "source_id": post_id,
                        "author": author,
                        "title": post.get("title", ""),
                        "content": content[:4000],
                        "raw_data": json.dumps(raw),
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
                print(f"  Request error for r/{subreddit_name} term '{term}': {e}", file=sys.stderr)
                results["errors"] += 1

            # Polite delay
            time.sleep(REQUEST_DELAY)

        print(f"  r/{subreddit_name}: {sub_count} posts collected")

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit PullPush scrape for cycle {cycle_id}...")
    print(f"  8 subs x 9 terms = 72 requests, ~{72 * REQUEST_DELAY}s estimated")
    stats = scrape_reddit(cycle_id)
    print(json.dumps(stats, indent=2))
