"""
V7 Pipeline — Reddit Scraper v3 via RSS feeds
Degraded mode: Reddit JSON/API is 403-blocked, but RSS feeds still work.
Limited to latest ~25 posts per feed, but we can hit multiple subs and sorts.

Usage: python3 reddit_rss.py [cycle_id]
"""
import os
import json
import sys
import time
import xml.etree.ElementTree as ET
import re
import html
from datetime import datetime, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SUBREDDITS = [
    "SaaS", "startups", "Entrepreneur", "indiehackers",
    "productivity", "personalfinance", "fitness", "selfimprovement",
    "apps", "technology", "Futurology", "ArtificialIntelligence",
]

# RSS search isn't available, but we can get /new, /hot, /top feeds
SORTS = ["new", "hot", "top"]

SEARCH_TERMS = [
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "alternative to", "struggling with",
    "someone should build", "why isn't there",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

NS = {"atom": "http://www.w3.org/2005/Atom"}


def parse_rss_feed(xml_text: str) -> list:
    """Parse Atom RSS feed from Reddit."""
    posts = []
    try:
        root = ET.fromstring(xml_text)
        for entry in root.findall("atom:entry", NS):
            title_el = entry.find("atom:title", NS)
            link_el = entry.find("atom:link", NS)
            author_el = entry.find("atom:author/atom:name", NS)
            content_el = entry.find("atom:content", NS)
            id_el = entry.find("atom:id", NS)
            
            title = title_el.text if title_el is not None else ""
            link = link_el.get("href", "") if link_el is not None else ""
            author = author_el.text if author_el is not None else ""
            content_raw = content_el.text if content_el is not None else ""
            post_id = id_el.text if id_el is not None else ""
            
            # Extract Reddit post ID from URL
            id_match = re.search(r'/comments/([a-z0-9]+)/', link)
            source_id = id_match.group(1) if id_match else post_id
            
            # Clean HTML from content
            content_text = re.sub(r'<[^>]+>', ' ', html.unescape(content_raw)).strip()
            content_text = re.sub(r'\s+', ' ', content_text)[:4000]
            
            posts.append({
                "title": title,
                "url": link,
                "author": author.replace("/u/", ""),
                "content": content_text,
                "source_id": source_id,
            })
    except ET.ParseError as e:
        print(f"  XML parse error: {e}", file=sys.stderr)
    return posts


def has_pain_signal(title: str, content: str) -> bool:
    """Check if post contains pain point signals."""
    text = (title + " " + content).lower()
    for term in SEARCH_TERMS:
        if term.lower() in text:
            return True
    # Additional signals
    signals = ["annoying", "broken", "expensive", "overpriced", "waste of", 
               "doesn't work", "can't find", "tired of", "fed up", "nightmare",
               "help me", "advice needed", "recommendation", "suggest", "what do you use"]
    return any(s in text for s in signals)


def scrape_reddit(cycle_id: int) -> dict:
    """Scrape Reddit via RSS feeds and write to Supabase."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total_fetched": 0, "pain_matched": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    total_feeds = len(SUBREDDITS) * len(SORTS)
    print(f"  {total_feeds} RSS feeds ({len(SUBREDDITS)} subs x {len(SORTS)} sorts)")

    for sub in SUBREDDITS:
        for sort in SORTS:
            url = f"https://www.reddit.com/r/{sub}/{sort}/.rss?limit=100"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                if resp.status_code != 200:
                    print(f"  {sub}/{sort}: HTTP {resp.status_code}", file=sys.stderr)
                    results["errors"] += 1
                    continue
                
                posts = parse_rss_feed(resp.text)
                results["total_fetched"] += len(posts)
                
                for post in posts:
                    source_id = post["source_id"]
                    if not source_id or source_id in seen_ids:
                        continue
                    seen_ids.add(source_id)
                    
                    # Filter for pain signals
                    if not has_pain_signal(post["title"], post["content"]):
                        continue
                    
                    results["pain_matched"] += 1
                    
                    record = {
                        "cycle_id": cycle_id,
                        "source": "reddit",
                        "source_url": post["url"],
                        "source_id": source_id,
                        "author": post["author"],
                        "title": post["title"][:500],
                        "content": post["content"][:4000],
                        "raw_data": json.dumps(post)[:10000],
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
                print(f"  RSS error {sub}/{sort}: {e}", file=sys.stderr)
                results["errors"] += 1
            
            # Polite delay
            time.sleep(1.5)

    # Also try search via RSS (some subs support it)
    print(f"  Attempting RSS search feeds...")
    for sub in SUBREDDITS[:4]:  # Top 4 subs only
        for term in SEARCH_TERMS[:5]:  # Top 5 terms only
            url = f"https://www.reddit.com/r/{sub}/search.rss?q={term.replace(' ', '+')}&restrict_sr=on&sort=new&t=week"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                if resp.status_code != 200:
                    continue
                posts = parse_rss_feed(resp.text)
                for post in posts:
                    source_id = post["source_id"]
                    if not source_id or source_id in seen_ids:
                        continue
                    seen_ids.add(source_id)
                    results["total_fetched"] += 1
                    results["pain_matched"] += 1  # already search-filtered
                    
                    record = {
                        "cycle_id": cycle_id,
                        "source": "reddit",
                        "source_url": post["url"],
                        "source_id": source_id,
                        "author": post["author"],
                        "title": post["title"][:500],
                        "content": post["content"][:4000],
                        "raw_data": json.dumps(post)[:10000],
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
            except:
                pass
            time.sleep(1)

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting Reddit RSS scraper (degraded mode) for cycle {cycle_id}...")
    print(f"  ⚠️ RSS模式: 每个feed约25条，已加痛点关键词过滤")
    stats = scrape_reddit(cycle_id)
    print(f"\nFinal results:")
    print(json.dumps(stats, indent=2))
