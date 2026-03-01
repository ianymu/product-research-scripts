"""
V7 Pipeline — X/Twitter Scraper via Apify (apidojo/tweet-scraper)
Uses searchTerms for both keyword search AND account monitoring (via from:username).

Usage: python3 apify_x.py [cycle_id]
"""
import os
import json
import sys
import time
from datetime import datetime, timedelta, timezone
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

ACTOR_ID = "apidojo/tweet-scraper"
MAX_AGE_DAYS = 15
MAX_TWEETS_PER_SEARCH = 30

# --- LLM pre-filter for content relevance ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
ENABLE_LLM_FILTER = bool(OPENAI_API_KEY)  # auto-enable if key available

_LLM_FILTER_PROMPT = """You are a product pain-point filter. Given a tweet, answer YES or NO.
YES = This tweet describes a real user pain point, product frustration, feature request, tool comparison, or SaaS/software complaint that could inspire a paid product.
NO = This is political, social commentary, entertainment, personal life, meme, joke, vague wish, or unrelated to software/products.

Only reply YES or NO. Nothing else."""


def passes_llm_filter(text: str) -> bool:
    """Quick GPT-4.1-mini check: is this a product pain point?"""
    if not ENABLE_LLM_FILTER or not text.strip():
        return True  # skip filter if no API key
    try:
        import urllib.request
        payload = json.dumps({
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "system", "content": _LLM_FILTER_PROMPT},
                {"role": "user", "content": text[:500]}
            ],
            "max_tokens": 3,
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        answer = result["choices"][0]["message"]["content"].strip().upper()
        return answer.startswith("YES")
    except Exception:
        return True  # on error, let it through

# --- Account monitoring via "from:username" search ---
TIER0_ACCOUNTS = [
    # Indie makers & builders (high product-pain signal)
    "levelsio", "dannypostma", "marclouvion", "mckaywrigley",
    "tibo_maker", "thepatwalls", "marc_louvion", "yaborumal",
    # AI labs (product announcements & user pain reactions)
    "OpenAI", "AnthropicAI", "cursor_ai",
    # Builder community voices
    "ProductHunt", "ycombinator", "garrytan",
    # AI builder/maker focused
    "swyx", "danshipper", "theRundownAI", "aiaborumal",
]

FILTERED_ACCOUNTS = [
    "bcherny", "_catwu", "_akhaliq", "rowancheung",
    "lennysan", "gregisenberg", "Jason", "karpathy",
    "drjimfan", "AndrewYNg",
]

FILTER_RULES = {
    "min_likes_within_6h": 100,
    "min_retweets_within_6h": 20,
    "min_likes_after_6h": 500,
    "min_retweets_after_6h": 50,
}

# --- Keyword searches for toC/fundable product signals ---
# Each query is AND-gated with product/tech context to avoid political/social noise
KEYWORD_SEARCHES = [
    # Pain signals with product context
    '("I wish there was" OR "someone should build" OR "why is there no") AND ("app" OR "tool" OR "SaaS" OR "software" OR "plugin" OR "extension" OR "API")',
    # Paying users frustrated with existing tools
    '("paying" OR "subscription" OR "$" OR "per month") AND ("frustrating" OR "broken" OR "terrible" OR "overpriced" OR "switching") AND ("app" OR "tool" OR "software")',
    # Product switching signals
    '("switched from" OR "migrated from" OR "replaced" OR "ditched") AND ("better" OR "cheaper" OR "faster" OR "alternative") AND ("tool" OR "app" OR "SaaS")',
    # Builder/launch signals
    '("just launched" OR "just shipped" OR "built this" OR "side project") AND ("users" OR "MRR" OR "revenue" OR "waitlist" OR "beta")',
    # AI tool specific pain
    '("AI tool" OR "AI agent" OR "LLM" OR "ChatGPT" OR "Claude" OR "Cursor") AND ("broken" OR "expensive" OR "unreliable" OR "limitation" OR "frustrating" OR "wish")',
]


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
    sb = SupabaseLite(SUPABASE_URL, SUPABASE_KEY) if USE_LITE else create_client(SUPABASE_URL, SUPABASE_KEY)
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

        # LLM content relevance filter (skip noise before writing to DB)
        if not passes_llm_filter(text):
            results["filtered_out"] += 1
            return

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
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting X/Twitter scrape for cycle {cycle_id}...")

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
