#!/usr/bin/env python3
"""
PJM V3 — Stale Task Monitor (EC2 Cron 22:00)
扫描 task_queue 中 in_progress 超过 N 小时的任务，发送 TG 告警。

用法:
  python3 check_stale_tasks.py              # 默认 6 小时
  python3 check_stale_tasks.py --hours 4    # 自定义阈值
"""
import os
import sys
import json
from datetime import datetime, timezone

try:
    import httpx
    HTTP_CLIENT = "httpx"
except ImportError:
    import urllib.request
    import urllib.error
    HTTP_CLIENT = "urllib"

# 铁律 #1: 所有 os.environ 读取必须加 .strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
TG_TOKEN = os.environ.get("TG_TOKEN_MAIN", "").strip()
TG_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "").strip()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _get(path: str) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if HTTP_CLIENT == "httpx":
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=HEADERS)
            resp.raise_for_status()
            return resp.json()
    else:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))


def send_tg(message: str):
    """Send alert to Telegram."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print(f"TG not configured. Message:\n{message}")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}).encode("utf-8")

    if HTTP_CLIENT == "httpx":
        with httpx.Client(timeout=10) as client:
            client.post(url, content=data, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


def check_stale(hours: int = 6):
    """Find and alert on stale in_progress tasks."""
    tasks = _get("task_queue?status=eq.in_progress&order=updated_at.asc")

    if not tasks:
        print("No in_progress tasks found.")
        return

    now = datetime.now(timezone.utc)
    stale = []

    for t in tasks:
        updated_str = t.get("updated_at", "")
        if not updated_str:
            continue
        # Parse ISO format
        updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        delta_hours = (now - updated).total_seconds() / 3600

        if delta_hours >= hours:
            stale.append({
                "project": t["project_id"],
                "title": t["title"],
                "hours": round(delta_hours, 1),
                "tier": t.get("assigned_tier", "unknown"),
            })

    if not stale:
        print(f"All {len(tasks)} in_progress tasks are fresh (< {hours}h).")
        return

    # Build alert message
    lines = [f"*PJM 告警: {len(stale)} 个任务超时*\n"]
    for s in stale:
        lines.append(f"• [{s['project']}] {s['title']}")
        lines.append(f"  停滞 {s['hours']}h, tier={s['tier']}")
    lines.append(f"\n请检查是否卡住或忘记更新状态。")

    message = "\n".join(lines)
    print(message)
    send_tg(message)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=6, help="Stale threshold in hours")
    args = parser.parse_args()
    check_stale(args.hours)
