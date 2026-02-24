"""
V7 Pipeline — Pipeline Status Sync
Updates pipeline_status table and notifies Orchestrator.
"""
import os
import json
import sys
from datetime import datetime
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def update_stage(cycle_id: int, stage: str, status: str,
                 direction_id: str = None, metadata: dict = None) -> dict:
    """Update pipeline stage status."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    now = datetime.utcnow().isoformat()
    record = {
        "cycle_id": cycle_id,
        "stage": stage,
        "status": status,
        "direction_id": direction_id,
        "metadata": json.dumps(metadata or {}),
        "updated_at": now,
    }

    if status == "running":
        record["started_at"] = now
    elif status in ("completed", "failed"):
        record["completed_at"] = now

    result = sb.table("pipeline_status").upsert(
        record, on_conflict="cycle_id,stage"
    ).execute()

    return {"updated": True, "stage": stage, "status": status}


def get_pipeline_status(cycle_id: int) -> list:
    """Get all stage statuses for a cycle."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    result = sb.table("pipeline_status").select("*").eq(
        "cycle_id", cycle_id
    ).order("stage").execute()
    return result.data


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python sync_pipeline_status.py <cycle_id> <stage> <status>")
        sys.exit(1)

    cycle_id = int(sys.argv[1])
    stage = sys.argv[2]
    status = sys.argv[3]
    result = update_stage(cycle_id, stage, status)
    print(json.dumps(result, indent=2))
