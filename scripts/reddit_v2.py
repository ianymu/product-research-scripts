"""
V7 Pipeline — Reddit Scraper v2 via trudax/reddit-scraper
Replaces apify_reddit.py which used old.reddit.com (now 403-blocked).

Usage: python3 reddit_v2.py [cycle_id]
"""
import os
import json
import sys
from datetime import datetime, timezone
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SUBREDDITS = [
    "SaaS", "startups", "Entrepreneur", "indiehackers",
    "productivity", "personalfinance", "fitness", "selfimprovement",
    "apps", "technology", "Futurology", "ArtificialIntelligence",
]

SEARCH_TERMS = [
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "looking for", "alternative to", "struggling with",
    "someone should build", "why isn't there",
    "I'd pay for", "can't believe there's no",
    "switched from", "shut up and take my money",
    "addicted to", "everyone is using", "went viral",
    "changed my life", "million users",
]

MAX_ITEMS = 2500


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via trudax/reddit-scraper and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    # Build search URLs for the dedicated Reddit scraper
    # trudax/reddit-scraper accepts: startUrls, searchPosts, sort, time, etc.
    search_urls = []
    for sub in SUBREDDITS:
        for term in SEARCH_TERMS:
            search_urls.append({
                "url": f"https://www.reddit.com/r/{sub}/search/?q={term.replace(' ', '+')}&restrict_sr=1&sort=new&t=week"
            })

    print(f"  {len(search_urls)} search URLs ({len(SUBREDDITS)} subs x {len(SEARCH_TERMS)} terms)")
    print(f"  Using trudax/reddit-scraper actor")

    # Run in batches to manage cost/time — take first 60 URLs (top 3 subs x 20 terms)
    # and if time permits, run more
    batch_size = 60
    for batch_start in range(0, min(len(search_urls), 240), batch_size):
        batch_urls = search_urls[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"  Batch {batch_num}: {len(batch_urls)} URLs...")

        try:
            run = client.actor("trudax/reddit-scraper").call(
                run_input={
                    "startUrls": batch_urls,
                    "maxItems": MAX_ITEMS // 4,  # per batch
                    "proxy": {
                        "useApifyProxy": True,
                    },
                    "maxPostCount": 25,  # per search page
                    "scrollTimeout": 20,
                },
                timeout_secs=900,
            )
            dataset = client.dataset(run["defaultDatasetId"])

            for item in dataset.iterate_items():
                post_id = item.get("id", "") or item.get("postId", "")
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                results["total"] += 1

                if results["written"] >= MAX_ITEMS:
                    break

                title = item.get("title", "")
                content = item.get("body", "") or item.get("selftext", "") or item.get("text", "") or title
                source_url = item.get("url", "") or item.get("permalink", "")
                if source_url and not source_url.startswith("http"):
                    source_url = "https://www.reddit.com" + source_url

                record = {
                    "cycle_id": cycle_id,
                    "source": "reddit",
                    "source_url": source_url,
                    "source_id": post_id,
                    "author": item.get("author", "") or item.get("username", ""),
                    "title": title[:500],
                    "content": content[:4000],
                    "raw_data": json.dumps(item)[:10000],
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

        except Exception as e:
            print(f"  Batch {batch_num} error: {e}", file=sys.stderr)
            results["errors"] += 1

        if results["written"] >= MAX_ITEMS:
            print(f"  Reached MAX_ITEMS ({MAX_ITEMS}), stopping.")
            break

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit v2 scraper (trudax) for cycle {cycle_id}...")
    stats = scrape_reddit(cycle_id)
    print(f"\nFinal results:")
    print(json.dumps(stats, indent=2))
