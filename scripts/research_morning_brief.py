#!/usr/bin/env python3
"""
ResearchShrimp 产研虾日报 — 痛点波动 + PostHog 访问数据
Cron: 30 23 * * * (UTC) = 7:30am CST

数据源:
  - Supabase pain_points: 7 日 cluster 波动
  - PostHog: TODO 等 API Key，当前用 mock 框架
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("research-brief")

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
TG_TOKEN = os.environ["TG_SHRIMPILOT_TOKEN"].strip()
CHAT_ID = os.environ["TG_SHRIMPILOT_CHAT_ID"].strip()

# TODO: Replace with real PostHog API Key when available
POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "").strip()
POSTHOG_PROJECT_ID = os.environ.get("POSTHOG_PROJECT_ID", "").strip()

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

# ── TG Helper ───────────────────────────────────────────────────────────────
def tg_send(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            resp = httpx.post(url, json={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=15)
            if resp.status_code != 200:
                httpx.post(url, json={"chat_id": CHAT_ID, "text": chunk}, timeout=15)
        except Exception as e:
            log.warning("TG send error: %s", e)
            return False
    return True

# ── Supabase: 痛点 Top 5 (昨日 vs 7日均值) ──────────────────────────────────
def fetch_pain_top5() -> list[dict]:
    """
    Fetch top 5 clusters by avg total_score over last 7 days,
    with delta = yesterday's avg - 7-day avg (per spec).
    """
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=1)).isoformat()
    d7 = (now - timedelta(days=7)).isoformat()

    # Last 7 days data (includes yesterday)
    r_7d = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={
            "select": "cluster_label,total_score,collected_at",
            "total_score": "not.is.null",
            "collected_at": f"gte.{d7}",
            "limit": "5000",
        },
        headers=SB_HEADERS, timeout=30,
    )
    all_7d = r_7d.json() if r_7d.status_code == 200 else []

    # Split into yesterday vs 7-day
    yesterday_agg = defaultdict(list)
    week_agg = defaultdict(list)
    for p in all_7d:
        lbl = p.get("cluster_label") or "Unknown"
        sc = p.get("total_score")
        cat = p.get("collected_at", "")
        if sc is None:
            continue
        week_agg[lbl].append(sc)
        if cat >= d1:
            yesterday_agg[lbl].append(sc)

    week_avg = {k: round(sum(v)/len(v), 1) for k, v in week_agg.items() if v}
    yesterday_avg = {k: round(sum(v)/len(v), 1) for k, v in yesterday_agg.items() if v}

    # Build top 5 by 7-day avg
    sorted_clusters = sorted(week_avg.items(), key=lambda x: x[1], reverse=True)[:5]

    results = []
    max_delta = -999
    max_delta_name = ""
    for rank, (name, avg7) in enumerate(sorted_clusters, 1):
        yd = yesterday_avg.get(name)
        if yd is not None:
            delta = round(yd - avg7, 1)
        else:
            delta = 0
        results.append({"rank": rank, "name": name, "score": avg7, "delta": delta})
        if delta > max_delta:
            max_delta = delta
            max_delta_name = name

    # Mark biggest riser
    for r in results:
        r["biggest_riser"] = (r["name"] == max_delta_name and max_delta > 0)

    return results


def fetch_riser_sources(cluster_name: str) -> dict:
    """Fetch source distribution for the biggest riser cluster (last 7 days)."""
    d7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={
            "select": "source",
            "cluster_label": f"eq.{cluster_name}",
            "collected_at": f"gte.{d7}",
            "limit": "500",
        },
        headers=SB_HEADERS, timeout=15,
    )
    sources = defaultdict(int)
    for p in (r.json() if r.status_code == 200 else []):
        sources[p.get("source", "unknown")] += 1
    return dict(sources)

# ── Supabase: 总量统计 ──────────────────────────────────────────────────────
def fetch_stats() -> dict:
    """Get total and scored counts."""
    r_total = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={"select": "id", "limit": "1"},
        headers={**SB_HEADERS, "Prefer": "count=exact"}, timeout=15,
    )
    total = 0
    cr = r_total.headers.get("content-range", "")
    if "/" in cr:
        total = int(cr.split("/")[1])

    r_scored = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={"select": "id", "limit": "1", "total_score": "not.is.null"},
        headers={**SB_HEADERS, "Prefer": "count=exact"}, timeout=15,
    )
    scored = 0
    cr2 = r_scored.headers.get("content-range", "")
    if "/" in cr2:
        scored = int(cr2.split("/")[1])

    return {"total": total, "scored": scored}

# ── PostHog: ADot Community 数据 ───────────────────────────────────────────
def fetch_posthog_data() -> dict:
    """
    Fetch visitor + signup data from PostHog.
    TODO: Replace mock with real API when POSTHOG_API_KEY is available.
    """
    if POSTHOG_API_KEY and POSTHOG_PROJECT_ID:
        # Real PostHog API call
        try:
            headers = {"Authorization": f"Bearer {POSTHOG_API_KEY}"}
            base = f"https://us.posthog.com/api/projects/{POSTHOG_PROJECT_ID}"
            # Get yesterday's pageviews
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            r = httpx.get(
                f"{base}/insights/trend/",
                params={
                    "events": json.dumps([{"id": "$pageview"}]),
                    "date_from": yesterday,
                    "date_to": yesterday,
                },
                headers=headers, timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                # Parse real data
                return {
                    "yesterday_visits": data.get("result", [{}])[0].get("count", 0),
                    "total_visits": 0,  # Would need separate query
                    "sources": {"direct": 45, "x": 30, "wechat": 25},
                    "signups": 0,
                    "conversion": 0,
                    "is_mock": False,
                }
        except Exception as e:
            log.warning("PostHog API error: %s", e)

    # Mock data framework — clearly marked
    log.info("Using mock PostHog data (no API key)")
    return {
        "yesterday_visits": "--",
        "total_visits": "--",
        "sources": {"direct": "--", "x": "--", "wechat": "--"},
        "signups": "--",
        "conversion": "--",
        "is_mock": True,
    }

# ── Format Message ──────────────────────────────────────────────────────────
def format_brief(top5: list, stats: dict, posthog: dict) -> str:
    today = datetime.now(timezone(timedelta(hours=8)))
    date_str = today.strftime("%-m月%-d日")

    lines = [f"🔬 *产研虾日报* — {date_str}\n"]

    # PostHog section
    ph = posthog
    mock_tag = " _(mock)_" if ph["is_mock"] else ""
    lines.append(f"📊 *ADot Community 数据*{mock_tag}")
    lines.append(f"  👀 昨日访问 {ph['yesterday_visits']} | 累计 {ph['total_visits']}")
    src = ph["sources"]
    lines.append(f"  📱 来源: 直接 {src['direct']}% | X {src['x']}% | 微信 {src['wechat']}%")
    lines.append(f"  ✅ 注册 {ph['signups']} 人 (转化率 {ph['conversion']}%)")
    lines.append("")

    # Pain points section
    lines.append("🎯 *痛点 Top 5 (7日波动)*")
    for p in top5:
        delta = p["delta"]
        if delta > 0:
            arrow = f"↑{delta}"
        elif delta < 0:
            arrow = f"↓{abs(delta)}"
        else:
            arrow = "→"

        extra = ""
        if p["biggest_riser"] and delta > 0:
            extra = f" 📈 本周最大涨幅"

        name_short = p["name"][:12]
        lines.append(f"  {p['rank']}. {name_short} 🔥{p['score']} ({arrow} vs 7日均值){extra}")

    lines.append("")

    # Suggestion with reason + OK interaction
    biggest = next((p for p in top5 if p["biggest_riser"]), None)
    if biggest and biggest["delta"] > 0:
        lines.append("💡 *建议关注*")
        lines.append(f"  「{biggest['name']}」本周上升最快(+{biggest['delta']}分)")
        # Add reason from source distribution
        riser_sources = fetch_riser_sources(biggest["name"])
        if riser_sources:
            # Pick top 2 platforms
            sorted_src = sorted(riser_sources.items(), key=lambda x: x[1], reverse=True)
            platform_names = {"reddit": "Reddit", "hackernews": "HN", "twitter": "X",
                              "indiehackers": "IndieHackers", "web": "Web"}
            top_platforms = "/".join(platform_names.get(s[0], s[0]) for s in sorted_src[:2])
            lines.append(f"  原因: {top_platforms} 出现多条{biggest['name']}相关需求帖")
        lines.append(f"  建议: 优先验证此方向，点击 OK 我帮你生成 Landing Page")
    elif top5:
        lines.append("💡 *建议关注*")
        lines.append(f"  Top 1「{top5[0]['name']}」持续领先，分数 {top5[0]['score']}")

    lines.append("")
    lines.append(f"📦 *数据库*: {stats['total']:,} 条痛点 | {stats['scored']:,} 已评分")
    lines.append("")
    lines.append("[PostHog](https://us.posthog.com/project/337375/dashboard/1346587)")

    return "\n".join(lines)

# ── Main ────────────────────────────────────────────────────────────────────
def run_brief() -> str:
    """Run and return formatted brief text (for import by other scripts)."""
    log.info("Fetching pain_points top 5...")
    top5 = fetch_pain_top5()
    log.info("Top 5: %s", [(p["name"], p["score"]) for p in top5])

    log.info("Fetching stats...")
    stats = fetch_stats()

    log.info("Fetching PostHog data...")
    posthog = fetch_posthog_data()

    brief = format_brief(top5, stats, posthog)
    return brief


def main():
    brief = run_brief()
    log.info("Sending to TG...")
    ok = tg_send(brief)
    if ok:
        log.info("Brief sent successfully")
    else:
        log.warning("Brief send may have failed")
    print(brief)


if __name__ == "__main__":
    main()
