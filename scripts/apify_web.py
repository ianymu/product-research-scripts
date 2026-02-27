"""
V7 Pipeline — IndieHackers Web Scraper via Apify
Uses apify/web-scraper with custom pageFunction.

Usage: python3 apify_web.py [cycle_id]
"""
import os
import json
import sys
from datetime import datetime, timezone
from apify_client import ApifyClient
try:
    from supabase import create_client
    USE_LITE = False
except ImportError:
    from supabase_lite import SupabaseLite, DuplicateError
    USE_LITE = True

APIFY_API_KEY = os.environ["APIFY_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

START_URLS = [
    "https://www.indiehackers.com/posts?sort=trending",
    "https://www.indiehackers.com/posts?sort=newest",
]
MAX_ITEMS = 600

# JavaScript pageFunction for IndieHackers post list pages
PAGE_FUNCTION = """
async function pageFunction(context) {
    const { $, request, log } = context;
    const results = [];
    // IndieHackers post cards
    $('article, div[class*="post"], a[href*="/post/"]').each((i, el) => {
        const $el = $(el);
        const titleEl = $el.find('h2, h3, [class*="title"]').first();
        const title = titleEl.text().trim();
        if (!title) return;
        const linkEl = $el.find('a[href*="/post/"]').first();
        const href = linkEl.attr('href') || '';
        const url = href.startsWith('http') ? href : 'https://www.indiehackers.com' + href;
        const author = $el.find('[class*="author"], [class*="user"]').first().text().trim();
        const body = $el.find('[class*="body"], [class*="content"], p').first().text().trim();
        const slug = href.split('/').pop() || '';
        results.push({
            title: title,
            url: url,
            author: author,
            body: body.substring(0, 4000),
            id: slug,
        });
    });
    return results;
}
"""


def scrape_indiehackers(cycle_id: int) -> dict:
    """Scrape IndieHackers via Apify Web Scraper."""
    client = ApifyClient(APIFY_API_KEY)
    sb = SupabaseLite(SUPABASE_URL, SUPABASE_KEY) if USE_LITE else create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    try:
        run = client.actor("apify/web-scraper").call(
            run_input={
                "startUrls": [{"url": u} for u in START_URLS],
                "pageFunction": PAGE_FUNCTION,
                "maxPagesPerCrawl": MAX_ITEMS,
                "proxy": {"useApifyProxy": True},
            },
            timeout_secs=1800,
        )
        dataset = client.dataset(run["defaultDatasetId"])

        for item in dataset.iterate_items():
            post_id = item.get("id", "") or item.get("url", "").split("/")[-1]
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            results["total"] += 1

            record = {
                "cycle_id": cycle_id,
                "source": "indiehackers",
                "source_url": item.get("url", ""),
                "source_id": post_id,
                "author": item.get("author", ""),
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

    except Exception as e:
        print(f"  IndieHackers scrape error: {e}", file=sys.stderr)
        results["errors"] += 1

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting IndieHackers scrape for cycle {cycle_id}...")
    stats = scrape_indiehackers(cycle_id)
    print(json.dumps(stats, indent=2))
