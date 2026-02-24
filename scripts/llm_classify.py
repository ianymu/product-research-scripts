"""
V7 Pipeline — LLM Classifier
Classifies pain points into categories using Claude API.
"""
import os
import json
import sys
import anthropic
from supabase import create_client

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

CATEGORIES = [
    "developer-tools", "productivity", "finance", "health",
    "education", "marketing", "ecommerce", "other"
]

BATCH_SIZE = 50


def classify_batch(records: list) -> list:
    """Classify a batch of pain points using Claude."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    items = [{"id": r["id"], "title": r.get("title", ""), "content": (r.get("content", "") or "")[:500]}
             for r in records]

    prompt = f"""Classify each item into exactly one category from: {', '.join(CATEGORIES)}

Items:
{json.dumps(items, indent=2)}

Respond with a JSON array: [{{"id": "...", "category": "..."}}]"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text
    # Extract JSON from response
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return [{"id": r["id"], "category": "other"} for r in records]


def classify_cycle(cycle_id: int) -> dict:
    """Classify all unprocessed pain points for a cycle."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Get unclassified records
    records = sb.table("pain_points").select("id, title, content").eq(
        "cycle_id", cycle_id
    ).is_("category", "null").limit(1000).execute().data

    results = {"total": len(records), "classified": 0, "errors": 0}

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        try:
            classified = classify_batch(batch)
            for item in classified:
                try:
                    sb.table("pain_points").update(
                        {"category": item["category"]}
                    ).eq("id", item["id"]).execute()
                    results["classified"] += 1
                except Exception:
                    results["errors"] += 1
        except Exception as e:
            print(f"  Batch classify error: {e}", file=sys.stderr)
            results["errors"] += len(batch)

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Classifying pain points for cycle {cycle_id}...")
    stats = classify_cycle(cycle_id)
    print(json.dumps(stats, indent=2))
