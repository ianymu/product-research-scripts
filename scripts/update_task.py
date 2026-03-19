#!/usr/bin/env python3
"""
PJM V3 — Task Queue Helper
CC / OpenClaw / Oz 三层共用的任务状态上报工具。

用法:
  # 创建新任务
  python3 update_task.py --project P11 --task "部署 ShrimPilot" --status pending --priority 1

  # 更新已有任务状态
  python3 update_task.py --project P11 --task "部署 ShrimPilot" --status completed

  # 查看项目所有任务
  python3 update_task.py --project P11 --list

  # 查看所有活跃任务
  python3 update_task.py --active
"""
import os
import sys
import json
import argparse
from datetime import datetime

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

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

VALID_STATUSES = ["pending", "in_progress", "blocked", "completed", "failed", "cancelled"]
VALID_TIERS = ["cc", "openclaw", "oz", "manual"]


def _request(method: str, path: str, data: dict = None) -> dict:
    """Make HTTP request to Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"

    if HTTP_CLIENT == "httpx":
        with httpx.Client(timeout=15) as client:
            if method == "GET":
                resp = client.get(url, headers=HEADERS)
            elif method == "POST":
                resp = client.post(url, headers=HEADERS, json=data)
            elif method == "PATCH":
                resp = client.patch(url, headers=HEADERS, json=data)
            else:
                raise ValueError(f"Unsupported method: {method}")
            resp.raise_for_status()
            return resp.json() if resp.text else {}
    else:
        req = urllib.request.Request(url, method=method, headers=HEADERS)
        if data:
            req.data = json.dumps(data).encode("utf-8")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            print(f"HTTP {e.code}: {body}", file=sys.stderr)
            sys.exit(1)


def create_task(project_id: str, title: str, status: str = "pending",
                priority: int = 3, tier: str = "cc", agent: str = None,
                description: str = None, tags: list = None,
                created_by: str = "cc") -> dict:
    """Create a new task in task_queue."""
    record = {
        "project_id": project_id,
        "title": title,
        "status": status,
        "priority": priority,
        "assigned_tier": tier,
        "created_by": created_by,
    }
    if agent:
        record["assigned_agent"] = agent
    if description:
        record["description"] = description
    if tags:
        record["tags"] = tags

    result = _request("POST", "task_queue", record)
    if isinstance(result, list) and result:
        result = result[0]
    print(f"✓ Task created: [{project_id}] {title} (status={status}, priority={priority})")
    return result


def update_task(project_id: str, title: str, status: str,
                result_data: dict = None) -> dict:
    """Update an existing task's status by project_id + title match."""
    # Find the task first
    encoded_title = title.replace(" ", "%20").replace('"', "%22")
    path = f"task_queue?project_id=eq.{project_id}&title=eq.{encoded_title}&order=created_at.desc&limit=1"
    tasks = _request("GET", path)

    if not tasks:
        print(f"⚠ Task not found: [{project_id}] {title}. Creating new one.", file=sys.stderr)
        return create_task(project_id, title, status)

    task_id = tasks[0]["id"]
    update_data = {"status": status}
    if result_data:
        update_data["result"] = result_data

    updated = _request("PATCH", f"task_queue?id=eq.{task_id}", update_data)
    if isinstance(updated, list) and updated:
        updated = updated[0]
    print(f"✓ Task updated: [{project_id}] {title} → {status}")
    return updated


def list_tasks(project_id: str = None, active_only: bool = False) -> list:
    """List tasks, optionally filtered by project."""
    if active_only:
        path = "task_queue?status=not.in.(completed,cancelled)&order=priority.asc,created_at.asc"
    elif project_id:
        path = f"task_queue?project_id=eq.{project_id}&order=priority.asc,created_at.asc"
    else:
        path = "task_queue?order=priority.asc,created_at.asc&limit=50"

    tasks = _request("GET", path)

    if not tasks:
        print("(no tasks found)")
        return []

    # Pretty print
    print(f"\n{'ID':<8} {'Project':<8} {'Status':<12} {'P':<3} {'Title':<40} {'Updated'}")
    print("-" * 90)
    for t in tasks:
        updated = t.get("updated_at", "")[:16] if t.get("updated_at") else ""
        print(f"{t['id'][:7]}  {t['project_id']:<8} {t['status']:<12} {t['priority']:<3} {t['title'][:40]:<40} {updated}")

    print(f"\nTotal: {len(tasks)} tasks")
    return tasks


def check_stale(hours: int = 6) -> list:
    """Find tasks that have been in_progress for too long."""
    path = f"task_queue?status=eq.in_progress&order=updated_at.asc"
    tasks = _request("GET", path)

    stale = []
    now = datetime.utcnow()
    for t in tasks:
        updated = datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00").replace("+00:00", ""))
        delta = (now - updated).total_seconds() / 3600
        if delta >= hours:
            stale.append({**t, "stale_hours": round(delta, 1)})

    if stale:
        print(f"\n⚠ {len(stale)} stale tasks (in_progress > {hours}h):")
        for t in stale:
            print(f"  [{t['project_id']}] {t['title']} — stale {t['stale_hours']}h")
    else:
        print(f"✓ No stale tasks (in_progress > {hours}h)")

    return stale


def main():
    parser = argparse.ArgumentParser(description="PJM V3 Task Queue Helper")
    parser.add_argument("--project", "-p", help="Project ID (e.g., P11)")
    parser.add_argument("--task", "-t", help="Task title")
    parser.add_argument("--status", "-s", choices=VALID_STATUSES, help="Task status")
    parser.add_argument("--priority", type=int, default=3, choices=[1, 2, 3, 4, 5],
                        help="Priority 1(urgent)-5(low), default 3")
    parser.add_argument("--tier", choices=VALID_TIERS, default="cc",
                        help="Execution tier (cc/openclaw/oz/manual)")
    parser.add_argument("--agent", help="Assigned agent ID")
    parser.add_argument("--description", "-d", help="Task description")
    parser.add_argument("--tags", nargs="+", help="Task tags")
    parser.add_argument("--created-by", default="cc", help="Creator (cc/openclaw/manual)")
    parser.add_argument("--list", "-l", action="store_true", help="List tasks for project")
    parser.add_argument("--active", "-a", action="store_true", help="List all active tasks")
    parser.add_argument("--stale", action="store_true", help="Check for stale in_progress tasks")
    parser.add_argument("--stale-hours", type=int, default=6, help="Stale threshold in hours")
    parser.add_argument("--result-json", help="JSON string for task result data")

    args = parser.parse_args()

    if args.active:
        list_tasks(active_only=True)
    elif args.stale:
        check_stale(args.stale_hours)
    elif args.list:
        if not args.project:
            print("Error: --project required with --list", file=sys.stderr)
            sys.exit(1)
        list_tasks(args.project)
    elif args.project and args.task and args.status:
        result_data = json.loads(args.result_json) if args.result_json else None
        # Check if task exists → update, else create
        encoded_title = args.task.replace(" ", "%20").replace('"', "%22")
        path = f"task_queue?project_id=eq.{args.project}&title=eq.{encoded_title}&limit=1"
        existing = _request("GET", path)
        if existing:
            update_task(args.project, args.task, args.status, result_data)
        else:
            create_task(args.project, args.task, args.status, args.priority,
                        args.tier, args.agent, args.description, args.tags,
                        args.created_by)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
