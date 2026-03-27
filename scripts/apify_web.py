"""
V7 Pipeline — IndieHackers Scraper via Apify
Uses jupri/indiehackers actor (Firestore-backed, no SPA rendering needed).

Usage: python3 apify_web.py [cycle_id]
"""
import os
import json
import sys
import time
from datetime import datetime, timezone
from apify_client import ApifyClient
from supabase import create_client

APIFY_API_KEY = os.environ["APIFY_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ACTOR_ID = "jupri/indiehackers"
MAX_ITEMS = 600


def scrape_indiehackers(cycle_id: int) -> dict:
    """Scrape IndieHackers via jupri/indiehackers actor and write to Supabase."""
    client = ApifyClient(APIFY_API_KEY)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    try:
        run = client.actor(ACTOR_ID).call(
            run_input={"limit": MAX_ITEMS},
            timeout_secs=1800,
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
                "source": "indiehackers",
                "source_url": f"https://www.indiehackers.com/post/{post_id}",
                "source_id": post_id,
                "author": item.get("author", ""),
                "title": item.get("title", ""),
                "content": (item.get("content", "") or item.get("title", ""))[:4000],
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

    except Exception as e:
        print(f"  IndieHackers scrape error: {e}", file=sys.stderr)
        results["errors"] += 1

    return results


MIN_TARGET = 300
MAX_RETRIES = 3
RETRY_DELAY = 300  # 5 minutes


def main():
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Starting IndieHackers scrape for cycle {cycle_id}...")

    result = {"written": 0}
    for attempt in range(1, MAX_RETRIES + 1):
        result = scrape_indiehackers(cycle_id)
        if result["written"] >= MIN_TARGET:
            print(f"✅ IH: {result['written']} records (target: {MIN_TARGET})")
            break
        print(f"⚠️ Attempt {attempt}/{MAX_RETRIES}: only {result['written']}/{MIN_TARGET}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    else:
        print(f"❌ IH: {result['written']}/{MIN_TARGET} after {MAX_RETRIES} attempts")

    print(json.dumps(result, indent=2))
    print(f"RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    main()
