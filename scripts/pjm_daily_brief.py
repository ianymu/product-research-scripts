#!/usr/bin/env python3
"""
PJM Daily Brief — Portfolio status push to Telegram.
Runs as EC2 cron job (08:30 CT) via OpenClaw jobs.json.

Reads Supabase + EC2 service health, computes project indicators,
sends compact daily brief to Telegram via Orchestrator Bot.
"""

import os
import sys
import json
import subprocess
from datetime import datetime, timedelta, timezone

# --- Environment ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

def get_supabase():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def safe_query(func, default=None):
    """Execute a Supabase query with error handling."""
    try:
        return func()
    except Exception as e:
        print(f"[WARN] Query failed: {e}")
        return default

def check_service(cmd, timeout=5):
    """Check if a local service responds."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return "down"

def health_icon(status):
    """Map status to indicator text."""
    icons = {
        "green": "OK",
        "yellow": "WARN",
        "red": "DOWN",
        "grey": "N/A",
        "blocked": "BLOCKED",
        "running": "RUNNING",
    }
    return icons.get(status, "?")

def main():
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).isoformat()
    lines = []
    alerts = []

    today_str = now.strftime("%m-%d")
    lines.append(f"Portfolio Brief — {today_str}")
    lines.append("")

    # --- V7 Pipeline ---
    cycle_info = "?"
    v7_status = "grey"
    try:
        cycles = sb.table("cycles").select("*").order("id", desc=True).limit(1).execute()
        if cycles.data:
            c = cycles.data[0]
            cid = c.get("id", "?")

            pp = sb.table("pain_points").select("id", count="exact").eq("cycle_id", cid).execute()
            pp_count = pp.count or 0

            ps = sb.table("pipeline_status").select("stage,status").eq("cycle_id", cid).order("updated_at", desc=True).limit(1).execute()
            stage = ps.data[0].get("stage", "?") if ps.data else "?"
            ps_status = ps.data[0].get("status", "?") if ps.data else "?"

            cycle_info = f"Cycle{cid} Stage{stage} ({pp_count}pcs)"

            created = c.get("created_at", "")
            if created:
                try:
                    ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_hours = (now - ct).total_seconds() / 3600
                    if age_hours < 24:
                        v7_status = "green"
                    elif age_hours < 48:
                        v7_status = "yellow"
                    else:
                        v7_status = "red"
                        alerts.append(f"V7: No new cycle in {int(age_hours)}h")
                except Exception:
                    v7_status = "yellow"
        else:
            cycle_info = "No cycles"
            v7_status = "red"
            alerts.append("V7: No cycles found")
    except Exception as e:
        cycle_info = f"Error: {e}"
        v7_status = "red"

    lines.append(f"V7: {cycle_info} [{health_icon(v7_status)}]")

    # --- Plan A (CI Agent) ---
    try:
        snaps = sb.table("ci_snapshots").select("id", count="exact").gte("created_at", yesterday).execute()
        snap_count = snaps.count or 0
        if snap_count > 0:
            pa_status = "green"
            pa_info = f"{snap_count} snapshots/24h"
        else:
            pa_status = "yellow"
            pa_info = "No snapshots/24h"
            alerts.append("Plan A: No CI snapshots in 24h")
    except Exception:
        pa_status = "grey"
        pa_info = "Query failed"
    lines.append(f"Plan A: {pa_info} [{health_icon(pa_status)}]")

    # --- Plan B (Reports) ---
    lines.append(f"Plan B: Blocked [{health_icon('blocked')}]")

    # --- Plan C (Apify Actors) ---
    lines.append(f"Plan C: Deployed [{health_icon('green')}]")

    # --- Plan D (MCP) ---
    lines.append(f"Plan D: Paused [{health_icon('grey')}]")

    # --- Plan E (V7 API) ---
    api_code = check_service("curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health")
    if api_code == "200":
        pe_status = "running"
        pe_info = "API :8080"
    else:
        pe_status = "red"
        pe_info = f"API down (HTTP {api_code})"
        alerts.append(f"Plan E: API down (HTTP {api_code})")
    lines.append(f"Plan E: {pe_info} [{health_icon(pe_status)}]")

    # --- Plan F (China) ---
    lines.append(f"Plan F: Draft [{health_icon('grey')}]")

    # --- Conway ---
    try:
        ops = sb.table("ops_log").select("id", count="exact").gte("created_at", yesterday).execute()
        ops_count = ops.count or 0
        if ops_count > 0:
            cw_status = "green"
            cw_info = f"{ops_count} ops/24h"
        else:
            cw_status = "yellow"
            cw_info = "No ops/24h"
    except Exception:
        cw_status = "grey"
        cw_info = "Query failed"
    lines.append(f"Conway: {cw_info} [{health_icon(cw_status)}]")

    lines.append("")

    # --- Top 3 Priorities (simplified) ---
    priorities = []

    # Priority logic: Red items first, then blocked items, then important items
    if pe_status == "red":
        priorities.append("Fix Plan E API (30min)")
    if v7_status in ("yellow", "red"):
        priorities.append("Check V7 Pipeline (30min)")
    if pa_status == "yellow":
        priorities.append("Verify Plan A CI (30min)")

    # Always suggest V7 decision if data is fresh
    if v7_status == "green":
        priorities.append("V7 GO decision review (1h)")

    # Plan B is always blocked — suggest fixing
    priorities.append("Plan B report fix (2h)")

    # Fill up to 3
    priorities = priorities[:3]

    if priorities:
        lines.append("Top 3 Today:")
        for i, p in enumerate(priorities, 1):
            lines.append(f"{i}. {p}")

    # --- Alerts ---
    if alerts:
        lines.append("")
        lines.append("Alerts:")
        for a in alerts:
            lines.append(f"- {a}")

    message = "\n".join(lines)

    # --- Send to Telegram ---
    try:
        from telegram_utils import send_as_bot
        ok = send_as_bot("main", message, parse_mode="")
        if ok:
            print("[OK] Daily brief sent to Telegram")
        else:
            print("[FAIL] send_as_bot returned False")
    except ImportError:
        print("[WARN] telegram_utils not available, printing to stdout:")
        print(message)
    except Exception as e:
        print(f"[FAIL] Telegram send error: {e}")
        print(message)

    return 0

if __name__ == "__main__":
    sys.exit(main())
