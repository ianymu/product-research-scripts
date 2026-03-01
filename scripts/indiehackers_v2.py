"""
V7 Pipeline — IndieHackers Scraper v2 via Playwright Scraper
Replaces apify_web.py which used jQuery (broken due to SPA).

Usage: python3 indiehackers_v2.py [cycle_id]
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

START_URLS = [
    "https://www.indiehackers.com/posts?sort=trending",
    "https://www.indiehackers.com/posts?sort=newest",
    "https://www.indiehackers.com/",
]
MAX_ITEMS = 600

# Playwright pageFunction — waits for SPA to render, then extracts
PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page, request, log } = context;
    
    // Wait for SPA content to render
    await page.waitForTimeout(3000);
    
    // Try to scroll to load more content
    for (let i = 0; i < 5; i++) {
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.waitForTimeout(1500);
    }
    
    // Extract all post-like elements
    const posts = await page.evaluate(() => {
        const results = [];
        // Try multiple selectors for IndieHackers posts
        const selectors = [
            'article', 
            '[data-testid*="post"]',
            'a[href*="/post/"]',
            '.feed-item',
            '[class*="post-card"]',
            '[class*="PostCard"]',
        ];
        
        const seen = new Set();
        
        for (const sel of selectors) {
            document.querySelectorAll(sel).forEach(el => {
                // Find title
                const titleEl = el.querySelector('h1, h2, h3, [class*="title"], [class*="Title"]');
                const title = titleEl ? titleEl.textContent.trim() : '';
                if (!title || seen.has(title)) return;
                seen.add(title);
                
                // Find link
                const linkEl = el.tagName === 'A' ? el : el.querySelector('a[href*="/post/"]');
                const href = linkEl ? linkEl.getAttribute('href') : '';
                const url = href && href.startsWith('/') ? 'https://www.indiehackers.com' + href : href;
                
                // Find author
                const authorEl = el.querySelector('[class*="author"], [class*="Author"], [class*="user"]');
                const author = authorEl ? authorEl.textContent.trim() : '';
                
                // Find body/preview
                const bodyEl = el.querySelector('[class*="body"], [class*="Body"], [class*="content"], [class*="preview"], p');
                const body = bodyEl ? bodyEl.textContent.trim().substring(0, 4000) : '';
                
                const slug = href ? href.split('/').pop() : '';
                
                results.push({ title, url, author, body, id: slug || title.substring(0, 50) });
            });
        }
        
        // Fallback: just get all links to /post/ pages
        if (results.length === 0) {
            document.querySelectorAll('a[href*="/post/"]').forEach(el => {
                const href = el.getAttribute('href') || '';
                const text = el.textContent.trim();
                if (text && text.length > 10 && !seen.has(text)) {
                    seen.add(text);
                    const url = href.startsWith('/') ? 'https://www.indiehackers.com' + href : href;
                    results.push({ title: text, url, author: '', body: '', id: href.split('/').pop() });
                }
            });
        }
        
        return results;
    });
    
    log.info(`Found ${posts.length} posts on ${request.url}`);
    return posts;
}
"""


def scrape_indiehackers(cycle_id: int) -> dict:
    """Scrape IndieHackers via Playwright Scraper."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    print(f"  Using apify/playwright-scraper for SPA rendering")

    try:
        run = client.actor("apify/playwright-scraper").call(
            run_input={
                "startUrls": [{"url": u} for u in START_URLS],
                "pageFunction": PAGE_FUNCTION,
                "maxPagesPerCrawl": MAX_ITEMS,
                "proxyConfiguration": {"useApifyProxy": True},
                "headless": True,
                "navigationTimeoutSecs": 30,
                "pageFunctionTimeoutSecs": 60,
            },
            timeout_secs=600,
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
                "source_id": post_id[:200],
                "author": item.get("author", ""),
                "title": (item.get("title", ""))[:500],
                "content": (item.get("body", "") or item.get("title", ""))[:4000],
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
        print(f"  Playwright scrape error: {e}", file=sys.stderr)
        results["errors"] += 1

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting IndieHackers v2 (Playwright) for cycle {cycle_id}...")
    stats = scrape_indiehackers(cycle_id)
    print(f"\nFinal results:")
    print(json.dumps(stats, indent=2))
