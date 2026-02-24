"""
V7 Pipeline — Supabase Writer
Generic write/upsert utility for any table.
"""
import os
import json
import sys
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def write_records(table: str, records: list, upsert: bool = False,
                  on_conflict: str = None) -> dict:
    """Write or upsert records to Supabase table."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    results = {"success": 0, "errors": []}

    for record in records:
        try:
            if upsert and on_conflict:
                sb.table(table).upsert(record, on_conflict=on_conflict).execute()
            else:
                sb.table(table).insert(record).execute()
            results["success"] += 1
        except Exception as e:
            results["errors"].append({"record_id": record.get("id", "unknown"), "error": str(e)})

    return results


def update_record(table: str, record_id: str, updates: dict) -> bool:
    """Update a single record by ID."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        sb.table(table).update(updates).eq("id", record_id).execute()
        return True
    except Exception as e:
        print(f"Update error: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Example: python supabase_write.py pain_points '{"cycle_id": 1, "source": "test"}'
    table = sys.argv[1] if len(sys.argv) > 1 else "pain_points"
    record_json = sys.argv[2] if len(sys.argv) > 2 else '{"test": true}'
    record = json.loads(record_json)
    result = write_records(table, [record])
    print(json.dumps(result, indent=2))
