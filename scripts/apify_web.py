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

APIFY_API_KEY = os.environ["APIFY_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

START_URLS = [
    "https://www.indiehackers.com/starting-up",
    "https://www.indiehackers.com/tech",
    "https://www.indiehackers.com/creators",
    "https://www.indiehackers.com/lifestyle",
    "https://www.indiehackers.com/money",
    "https://www.indiehackers.com/products",
    "https://www.indiehackers.com/ideas",
    "https://www.indiehackers.com/stories",
]
MAX_ITEMS = 600

# JavaScript pageFunction for IndieHackers with Puppeteer
PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page, request, log, enqueueLinks } = context;
    
    // Wait for content to load - IndieHackers uses heavy client-side rendering
    await new Promise(resolve => setTimeout(resolve, 5000));
    
    // Try to wait for navigation/content elements
    try {
        await page.waitForSelector('a, article, main', { timeout: 10000 });
    } catch (e) {
        log.warning('Timeout waiting for content elements');
    }
    
    const url = request.url;
    
    // If this is a topic/category page, scrape the content
    const isTopicPage = url.includes('/starting-up') || url.includes('/tech') || 
                        url.includes('/creators') || url.includes('/lifestyle') ||
                        url.includes('/money') || url.includes('/products') ||
                        url.includes('/ideas') || url.includes('/stories') ||
                        url.includes('/posts');
    
    if (isTopicPage) {
        // Scroll the page to trigger lazy loading
        await page.evaluate(() => {
            window.scrollTo(0, document.body.scrollHeight / 2);
        });
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        // Extract all text content from the topic page
        const content = await page.evaluate(() => {
            const main = document.querySelector('main, [role="main"], article');
            return main ? main.textContent.trim() : document.body.textContent.trim();
        });
        
        const title = await page.evaluate(() => {
            const h1 = document.querySelector('h1');
            return h1 ? h1.textContent.trim() : document.title;
        });
        
        log.info(`Scraped topic page: ${title.substring(0, 50)}...`);
        
        return {
            url: url,
            title: title.substring(0, 500),
            content: content.substring(0, 4000),
            author: '',
            scraped_at: new Date().toISOString(),
        };
    }
    
    // Extract individual post content
    const data = await page.evaluate(() => {
        const url = window.location.href;
        
        // Try multiple selectors for title
        let title = '';
        const titleEl = document.querySelector('h1, h2[class*="title"], [class*="post-title"]');
        if (titleEl) title = titleEl.textContent.trim();
        if (!title) {
            const titleTag = document.querySelector('title');
            if (titleTag) title = titleTag.textContent.trim();
        }
        
        // Extract content
        let content = '';
        const contentEl = document.querySelector('article, [class*="post-body"], [class*="content"], main');
        if (contentEl) content = contentEl.textContent.trim();
        
        // Extract author
        let author = '';
        const authorEl = document.querySelector('[class*="author"], [class*="username"], a[href*="/@"]');
        if (authorEl) author = authorEl.textContent.trim();
        
        return { url, title, content, author };
    });
    
    log.info(`Scraped post: ${data.title.substring(0, 50)}...`);
    
    return {
        url: data.url,
        title: data.title.substring(0, 500),
        content: data.content.substring(0, 4000),
        author: data.author.substring(0, 100),
        scraped_at: new Date().toISOString(),
    };
}
"""


def scrape_indiehackers(cycle_id: int) -> dict:
    """Scrape IndieHackers via Apify Puppeteer Scraper."""
    client = ApifyClient(APIFY_API_KEY)
    sb = SupabaseLite(SUPABASE_URL, SUPABASE_KEY) if USE_LITE else create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    try:
        run = client.actor("apify/puppeteer-scraper").call(
            run_input={
                "startUrls": [{"url": u} for u in START_URLS],
                "pageFunction": PAGE_FUNCTION,
                "maxRequestsPerCrawl": MAX_ITEMS,
                "proxyConfiguration": {"useApifyProxy": True},
                "waitUntil": ["networkidle2"],
            },
            timeout_secs=1800,
        )
        dataset = client.dataset(run["defaultDatasetId"])

        for item in dataset.iterate_items():
            url = item.get("url", "")
            if not url:
                continue
                
            # Generate ID from URL
            post_id = url.split("/")[-1] or url.split("?")[0].split("/")[-1] or str(results["total"])
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            results["total"] += 1
            
            title = item.get("title", "")
            content = item.get("content", "")
            author = item.get("author", "")

            record = {
                "cycle_id": cycle_id,
                "source": "indiehackers",
                "source_url": url,
                "source_id": post_id,
                "author": author[:200],
                "title": title[:500] if title else "IndieHackers Post",
                "content": content[:4000],
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
