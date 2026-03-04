"""
V7 Pipeline — IndieHackers Collector via Algolia Search API (free, no Apify needed)
Uses IH's public Algolia search-only key to query discussions directly.

Usage:
  python3 ih_collector.py [cycle_id]                     # daily cron (hardcoded queries)
  python3 ih_collector.py 2001 --queries-file q.json     # focused collection (custom queries)
"""
import os
import json
import sys
import argparse
import requests
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

ALGOLIA_APP_ID = "N86T1R3OWZ"
ALGOLIA_API_KEY = "5140dac5e87f47346abbda1a34ee70c3"
ALGOLIA_INDEX = "discussions"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

DEFAULT_SEARCH_TERMS = [
    "pain point", "frustrating", "wish there was",
    "paying for", "need a tool", "hate using",
    "looking for", "alternative to", "struggling with",
    "someone should build", "why isn't there",
    "I'd pay for", "can't believe there's no",
    "switched from", "shut up and take my money",
    "addicted to", "everyone is using", "went viral",
    "changed my life", "million users",
]
HITS_PER_QUERY = 50

# Parse CLI args
parser = argparse.ArgumentParser(description="V7 IndieHackers Collector")
parser.add_argument("cycle_id", nargs="?", type=int, default=1)
parser.add_argument("--queries-file", type=str, default=None,
                    help="JSON file with custom search_terms for focused collection")
_args = parser.parse_args()

if _args.queries_file:
    with open(_args.queries_file) as _f:
        _custom = json.load(_f)
    SEARCH_TERMS = _custom.get("search_terms", DEFAULT_SEARCH_TERMS)
else:
    SEARCH_TERMS = DEFAULT_SEARCH_TERMS


def collect_ih(cycle_id: int) -> dict:
    """Collect IndieHackers discussions via Algolia search API."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }

    results = {"total": 0, "written": 0, "duplicates": 0, "errors": 0}
    seen_ids = set()

    for term in SEARCH_TERMS:
        try:
            r = requests.post(ALGOLIA_URL, headers=headers, json={
                "query": term,
                "hitsPerPage": HITS_PER_QUERY,
            }, timeout=30)
            r.raise_for_status()
            data = r.json()

            for hit in data.get("hits", []):
                post_id = hit.get("itemKey", "") or hit.get("objectID", "")
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                results["total"] += 1

                title_parts = []
                if hit.get("title"):
                    title_parts.append(hit["title"])
                body = hit.get("body", "") or ""

                record = {
                    "cycle_id": cycle_id,
                    "source": "indiehackers",
                    "source_url": f"https://www.indiehackers.com/post/{post_id}",
                    "source_id": str(post_id),
                    "author": hit.get("authorUsername", "") or "",
                    "title": " ".join(title_parts) if title_parts else body[:140],
                    "content": body[:4000],
                    "raw_data": json.dumps(hit, default=str),
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
            print(f"  Search error [{term}]: {e}", file=sys.stderr)
            results["errors"] += 1

    return results


def main():
    cycle_id = _args.cycle_id
    print(f"Starting IndieHackers Algolia collection for cycle {cycle_id}...")
    print(f"  {len(SEARCH_TERMS)} queries x {HITS_PER_QUERY} hits/query")
    if _args.queries_file:
        print(f"  [FOCUSED] Using custom queries from {_args.queries_file}")

    result = collect_ih(cycle_id)
    print(f"\nIH result: {result['written']} written, {result['duplicates']} dups, {result['errors']} errors")
    print(json.dumps(result, indent=2))
    print(f"RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    main()
