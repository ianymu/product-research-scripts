"""
V7 Pipeline — Reddit Pain Point Scraper via Apify Web Scraper
Uses apify/web-scraper (free) to crawl old.reddit.com search pages.

Usage: python3 apify_reddit.py [cycle_id]
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
MAX_ITEMS = 2500

# JavaScript pageFunction for old.reddit.com search results
PAGE_FUNCTION = """
async function pageFunction(context) {
    const { $, request, log } = context;
    const results = [];
    $('div.search-result-link').each((i, el) => {
        const $el = $(el);
        const title = $el.find('a.search-title').text().trim();
        const url = $el.find('a.search-title').attr('href') || '';
        const author = $el.find('a.author').text().trim();
        const selftext = $el.find('div.search-result-body').text().trim();
        const id = url.match(/comments\\/([a-z0-9]+)\\//);
        results.push({
            title: title,
            url: url.startsWith('http') ? url : 'https://old.reddit.com' + url,
            author: author,
            selftext: selftext,
            id: id ? id[1] : '',
            searchUrl: request.url,
        });
    });
    return results;
}
"""


def build_search_urls():
    """Build old.reddit.com search URLs for all sub+term combinations."""
    urls = []
    for sub in SUBREDDITS:
        for term in SEARCH_TERMS:
            url = f"https://old.reddit.com/r/{sub}/search?q={term.replace(' ', '+')}&restrict_sr=on&sort=relevance&t=week"
            urls.append({"url": url})
    return urls


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via Apify Web Scraper and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    start_urls = build_search_urls()
    print(f"  {len(start_urls)} search URLs ({len(SUBREDDITS)} subs x {len(SEARCH_TERMS)} terms)")

    try:
        run = client.actor("apify/web-scraper").call(
            run_input={
                "startUrls": start_urls,
                "pageFunction": PAGE_FUNCTION,
                "maxPagesPerCrawl": MAX_ITEMS,
                "proxy": {"useApifyProxy": True},
                "maxConcurrency": 5,
            },
            timeout_secs=3600,
        )
        dataset = client.dataset(run["defaultDatasetId"])

        for item in dataset.iterate_items():
            post_id = item.get("id", "")
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            results["total"] += 1

            record = {
                "cycle_id": cycle_id,
                "source": "reddit",
                "source_url": item.get("url", ""),
                "source_id": post_id,
                "author": item.get("author", ""),
                "title": item.get("title", ""),
                "content": (item.get("selftext", "") or item.get("title", ""))[:4000],
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
                    print(f"  Write error: {e}", file=sys.stderr)

    except Exception as e:
        print(f"  Reddit scrape error: {e}", file=sys.stderr)
        results["errors"] += 1

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit web-scraper for cycle {cycle_id}...")
    stats = scrape_reddit(cycle_id)
    print(json.dumps(stats, indent=2))
