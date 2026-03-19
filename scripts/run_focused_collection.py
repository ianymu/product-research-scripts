#!/usr/bin/env python3
"""
V7 Pipeline — Focused Collection: One-command Stage 1 + Stage 2 for a specific topic.

Does NOT affect the daily 08:00 cron. Uses --queries-file mechanism added to each collector.

Usage:
  python3 run_focused_collection.py --topic "独立创业者社群平台"
  python3 run_focused_collection.py --topic "AI fitness coach" --cycle 2005

Flow:
  1. LLM generates 4-platform custom queries based on topic
  2. Allocates new cycle_id (max existing + 1, starting from 2001)
  3. Runs 4 collectors in parallel with --queries-file
  4. Runs Stage 2 (v7_stage2_full.py --cycle N)
  5. Sends TG notification with results
  6. Cleans up temp files
"""
import os
import sys
import json
import argparse
import tempfile
import subprocess
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Iron rule: .strip() all env vars ---
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Telegram notification (inline, no import dependency on telegram_utils location)
TG_TOKEN = os.environ.get("TG_TOKEN_DATA", "").strip()
TG_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "").strip()


def tg_send(text: str) -> bool:
    """Send Telegram message via DataBot."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] No token/chat_id configured, skipping notification")
        return False
    import urllib.request
    import urllib.parse
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[TG] Send error: {e}")
        return False


def allocate_cycle_id() -> int:
    """Get next cycle_id from Supabase. Focused cycles start at 2001."""
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = sb.table("pain_points").select("cycle_id").order("cycle_id", desc=True).limit(1).execute()
    if res.data:
        max_id = res.data[0]["cycle_id"]
        return max(max_id + 1, 2001)
    return 2001


def generate_queries(topic: str) -> dict:
    """Use Claude Haiku to generate platform-specific search queries for the topic."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

    prompt = f"""You are a product research assistant. Generate search queries for investigating this product direction across 4 platforms.

Direction/Topic: {topic}

Output ONLY valid JSON (no markdown, no explanation) in this exact format:
{{
  "reddit": {{
    "subreddits": ["sub1", "sub2", ...],
    "search_terms": ["term1", "term2", ...]
  }},
  "hn": {{
    "queries": ["query1", "query2", ...]
  }},
  "ih": {{
    "search_terms": ["term1", "term2", ...]
  }},
  "x": {{
    "keyword_searches": ["query1", "query2", ...]
  }}
}}

Requirements:
- All search terms must be in ENGLISH
- Reddit: 5-10 relevant subreddits (just the name, NO "r/" prefix, e.g. "startups" not "r/startups") + 10-15 search terms
- HN: 8-12 Algolia search queries
- IH: 8-12 search terms
- X/Twitter: 5-8 advanced search queries using AND/OR syntax
- Cover: user pain points, competitor names, related scenarios, emotional expressions
- Include trigger phrases: "pain point", "frustrating", "wish there was", "looking for", "alternative to"
- Include specific product names and industry jargon relevant to the topic
- Include signals: "accountability", "loneliness", "community", "co-working" etc. as relevant
- IMPORTANT: Subreddit names must NOT include "r/" prefix"""

    print("  Calling Claude Haiku to generate queries...")
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        max_completion_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]  # remove first line
        if raw.endswith("```"):
            raw = raw[:-3]
        elif "```" in raw:
            raw = raw[:raw.rfind("```")]
    raw = raw.strip()

    queries = json.loads(raw)

    # Print summary
    r_subs = len(queries.get("reddit", {}).get("subreddits", []))
    r_terms = len(queries.get("reddit", {}).get("search_terms", []))
    hn_q = len(queries.get("hn", {}).get("queries", []))
    ih_t = len(queries.get("ih", {}).get("search_terms", []))
    x_k = len(queries.get("x", {}).get("keyword_searches", []))
    print(f"  Generated: Reddit({r_subs} subs, {r_terms} terms), HN({hn_q} queries), IH({ih_t} terms), X({x_k} searches)")

    return queries


def write_query_files(queries: dict, tmpdir: str) -> dict:
    """Write platform-specific query JSON files. Returns {platform: filepath}."""
    files = {}
    for platform, data in queries.items():
        path = os.path.join(tmpdir, f"queries_{platform}.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        files[platform] = path
    return files


def run_collector(script_name: str, cycle_id: int, queries_file: str) -> dict:
    """Run a single collector script. Returns parsed result dict."""
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    cmd = ["python3", script_path, str(cycle_id), "--queries-file", queries_file]

    print(f"\n{'='*60}")
    print(f"  Starting {script_name} (cycle {cycle_id})...")
    print(f"  CMD: {' '.join(cmd)}")
    print(f"{'='*60}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min per collector (Apify actors can be slow)
            env=os.environ.copy(),
        )

        # Print stdout
        if proc.stdout:
            for line in proc.stdout.strip().split("\n"):
                print(f"  [{script_name}] {line}")

        # Print stderr (warnings/errors)
        if proc.stderr:
            for line in proc.stderr.strip().split("\n"):
                print(f"  [{script_name} ERR] {line}")

        # Parse RESULT line
        for line in reversed(proc.stdout.strip().split("\n")):
            if line.startswith("RESULT:"):
                return json.loads(line[7:])

        return {"written": 0, "errors": 1, "note": f"exit_code={proc.returncode}"}
    except subprocess.TimeoutExpired:
        print(f"  [{script_name}] TIMEOUT after 900s")
        return {"written": 0, "errors": 1, "note": "timeout"}
    except Exception as e:
        print(f"  [{script_name}] EXCEPTION: {e}")
        return {"written": 0, "errors": 1, "note": str(e)}


def run_stage2(cycle_id: int) -> bool:
    """Run v7_stage2_full.py for the given cycle."""
    # v7_stage2_full.py lives at ~/v7_stage2_full.py (not in ~/scripts/)
    candidates = [
        os.path.expanduser("~/v7_stage2_full.py"),
        os.path.join(SCRIPTS_DIR, "v7_stage2_full.py"),
    ]
    script_path = next((p for p in candidates if os.path.exists(p)), candidates[0])
    cmd = ["python3", script_path, "--cycle", str(cycle_id)]

    print(f"\n{'='*60}")
    print(f"  Starting Stage 2 (cycle {cycle_id})...")
    print(f"  CMD: {' '.join(cmd)}")
    print(f"{'='*60}")

    try:
        proc = subprocess.run(
            cmd,
            timeout=1800,  # 30 min for Stage 2
            env=os.environ.copy(),
        )
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        print("  Stage 2 TIMEOUT after 1800s")
        return False
    except Exception as e:
        print(f"  Stage 2 EXCEPTION: {e}")
        return False


def register_cycle(cycle_id: int, topic: str):
    """Register this focused cycle in pipeline_status."""
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    sb.table("pipeline_status").insert({
        "cycle_id": cycle_id,
        "stage": "collection",
        "status": "running",
        "metadata": json.dumps({
            "type": "focused",
            "topic": topic,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }),
    }).execute()


def main():
    parser = argparse.ArgumentParser(description="V7 Focused Collection — Stage 1 + Stage 2 for a specific topic")
    parser.add_argument("--topic", required=True, help="Product direction to investigate (e.g. '独立创业者社群平台')")
    parser.add_argument("--cycle", type=int, default=None, help="Override cycle_id (auto-allocated if not specified)")
    args = parser.parse_args()

    topic = args.topic
    start_time = time.time()
    print(f"\n{'#'*70}")
    print(f"#  V7 Focused Collection")
    print(f"#  Topic: {topic}")
    print(f"#  Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'#'*70}\n")

    # Step 1: Allocate cycle_id
    cycle_id = args.cycle if args.cycle else allocate_cycle_id()
    print(f"[1/5] Cycle ID: {cycle_id}")

    # Step 2: Generate queries via LLM
    print(f"\n[2/5] Generating platform-specific queries via Claude Haiku...")
    queries = generate_queries(topic)

    # Step 3: Write temp query files & register cycle
    tmpdir = tempfile.mkdtemp(prefix="v7_focused_")
    query_files = write_query_files(queries, tmpdir)
    print(f"\n  Temp dir: {tmpdir}")
    for platform, path in query_files.items():
        print(f"    {platform}: {path}")

    register_cycle(cycle_id, topic)
    tg_send(f"🎯 *定制采集启动*\n方向: {topic}\nCycle: {cycle_id}\n\n4 平台并行采集中...")

    # Step 4: Run 4 collectors in parallel
    print(f"\n[3/5] Running 4 collectors in parallel (cycle {cycle_id})...")

    collector_configs = [
        ("apify_reddit.py", query_files.get("reddit", "")),
        ("hn_collector.py", query_files.get("hn", "")),
        ("ih_collector.py", query_files.get("ih", "")),
        ("apify_x.py", query_files.get("x", "")),
    ]

    all_results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for script_name, qf in collector_configs:
            if qf:  # only run if query file exists for this platform
                f = executor.submit(run_collector, script_name, cycle_id, qf)
                futures[f] = script_name

        for future in as_completed(futures):
            script_name = futures[future]
            try:
                result = future.result()
                all_results[script_name] = result
            except Exception as e:
                print(f"  {script_name} failed: {e}")
                all_results[script_name] = {"written": 0, "errors": 1}

    # Summary
    total_written = sum(r.get("written", 0) for r in all_results.values())
    total_errors = sum(r.get("errors", 0) for r in all_results.values())
    total_dupes = sum(r.get("duplicates", 0) for r in all_results.values())

    stage1_elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  Stage 1 Complete!")
    print(f"  Total written: {total_written}")
    print(f"  Duplicates: {total_dupes}")
    print(f"  Errors: {total_errors}")
    print(f"  Time: {stage1_elapsed:.0f}s")
    print(f"{'='*60}")

    per_platform = "\n".join(f"  • {name}: {r.get('written', 0)} written" for name, r in all_results.items())
    tg_send(
        f"📡 *Stage 1 采集完毕*\n"
        f"方向: {topic} | Cycle: {cycle_id}\n"
        f"总计: {total_written} 条 | 耗时: {stage1_elapsed:.0f}s\n\n"
        f"{per_platform}"
    )

    if total_written == 0:
        msg = f"❌ Cycle {cycle_id} 采集 0 条数据，跳过 Stage 2"
        print(f"\n{msg}")
        tg_send(msg)
        cleanup(tmpdir)
        sys.exit(1)

    # Step 5: Run Stage 2
    print(f"\n[4/5] Running Stage 2 analysis (cycle {cycle_id})...")
    stage2_ok = run_stage2(cycle_id)

    # Step 6: Cleanup
    print(f"\n[5/5] Cleaning up temp files...")
    cleanup(tmpdir)

    total_elapsed = time.time() - start_time
    if stage2_ok:
        print(f"\n✅ Focused collection complete! Cycle {cycle_id}, {total_written} records, {total_elapsed:.0f}s total")
        tg_send(
            f"✅ *定制采集+分析完成*\n"
            f"方向: {topic} | Cycle: {cycle_id}\n"
            f"采集: {total_written} 条 | 总耗时: {total_elapsed:.0f}s\n\n"
            f"请查看评估卡，决策：GO / KILL / MAYBE"
        )
    else:
        print(f"\n⚠️ Stage 2 had errors. Check logs above. Cycle {cycle_id}")
        tg_send(
            f"⚠️ *定制采集完成，分析有错误*\n"
            f"方向: {topic} | Cycle: {cycle_id}\n"
            f"采集: {total_written} 条\n"
            f"请检查 Stage 2 日志"
        )


def cleanup(tmpdir: str):
    """Remove temp query files."""
    import shutil
    try:
        shutil.rmtree(tmpdir)
        print(f"  Cleaned up {tmpdir}")
    except Exception as e:
        print(f"  Cleanup warning: {e}")


if __name__ == "__main__":
    main()
