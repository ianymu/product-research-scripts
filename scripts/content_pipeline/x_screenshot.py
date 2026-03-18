#!/usr/bin/env python3
"""
content_pipeline/x_screenshot.py — X 大神推文截图
仅对 config.py 中已确认的 13 个大号截图，其他不截

方案:
  1. 用 Perplexity 找到大神与热点相关的推文 URL
  2. 用 tweet 截图服务 (tweetpik.com 或 类似) 获取截图
  3. 存入 EC2 ~/content-images/ 或 Supabase Storage

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging
import hashlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import X_ACCOUNTS, perplexity_search, SUPABASE_URL, SUPABASE_KEY, log

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# Screenshot output dir
SCREENSHOT_DIR = Path(os.path.expanduser("~/content-images/x-screenshots"))


def find_relevant_tweets(topic: str, keywords: list[str], max_results: int = 3) -> list[dict]:
    """
    Find tweets from verified big accounts related to the hotspot topic.
    Only returns tweets from X_ACCOUNTS (verified handles).
    """
    accounts_str = ", ".join(f"@{a}" for a in X_ACCOUNTS)
    query = (
        f"Find specific tweets from these X/Twitter accounts: {accounts_str} "
        f"that discuss '{topic}' or related keywords: {', '.join(keywords[:5])}. "
        f"Return the exact tweet URL (https://x.com/username/status/...) for each match. "
        f"Only include tweets from the last 7 days."
    )

    result = perplexity_search(query)
    if not result["answer"]:
        return []

    # Extract tweet URLs from response
    import re
    urls = re.findall(r'https?://(?:x\.com|twitter\.com)/(\w+)/status/(\d+)', result["answer"])

    tweets = []
    for handle, tweet_id in urls:
        # Only include verified accounts
        if handle.lower() in [a.lower() for a in X_ACCOUNTS]:
            tweets.append({
                "handle": handle,
                "tweet_id": tweet_id,
                "url": f"https://x.com/{handle}/status/{tweet_id}",
            })
            if len(tweets) >= max_results:
                break

    log.info(f"Found {len(tweets)} relevant tweets from verified accounts for '{topic}'")
    return tweets


def screenshot_tweet(tweet_url: str, output_path: Path = None) -> str:
    """
    Take a screenshot of a tweet.
    Uses tweetpik.com free tier or similar service.

    Returns: local file path of the screenshot, or empty string on failure.
    """
    tweet_id = tweet_url.split("/")[-1]

    if output_path is None:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = SCREENSHOT_DIR / f"tweet_{tweet_id}.png"

    # If already cached, return
    if output_path.exists():
        log.info(f"  Screenshot cached: {output_path}")
        return str(output_path)

    # Method 1: Use tweetpik-style URL for screenshot
    # Many free services render tweets as images
    screenshot_url = f"https://tweetpik.com/api/images/{tweet_id}"

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(screenshot_url)
            if resp.status_code == 200 and len(resp.content) > 1000:
                output_path.write_bytes(resp.content)
                log.info(f"  Screenshot saved: {output_path}")
                return str(output_path)
    except Exception as e:
        log.warning(f"  tweetpik failed: {e}")

    # Method 2: Fallback — use a public embed screenshot service
    embed_url = f"https://publish.twitter.com/oembed?url={tweet_url}"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(embed_url)
            if resp.status_code == 200:
                oembed = resp.json()
                # Store the embed HTML for rendering later
                html_path = output_path.with_suffix(".html")
                html_path.write_text(oembed.get("html", ""))
                log.info(f"  Embed HTML saved: {html_path}")
                return str(html_path)
    except Exception as e:
        log.warning(f"  Embed fallback also failed: {e}")

    log.error(f"  Could not screenshot: {tweet_url}")
    return ""


def get_screenshots_for_topic(topic: str, keywords: list[str]) -> list[dict]:
    """
    Main function: find and screenshot relevant X big-name tweets for a topic.

    Returns: list of {handle, tweet_url, screenshot_path}
    """
    tweets = find_relevant_tweets(topic, keywords)
    results = []

    for tweet in tweets:
        path = screenshot_tweet(tweet["url"])
        if path:
            results.append({
                "handle": tweet["handle"],
                "tweet_url": tweet["url"],
                "tweet_id": tweet["tweet_id"],
                "screenshot_path": path,
            })

    return results
