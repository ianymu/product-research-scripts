#!/usr/bin/env python3
"""
hotspot/trend_analyzer.py — 7日跨平台趋势分析
查询 Supabase 最近7天 content_hotspots → 计算趋势 → 分类 rising/falling/stable/breakout
"""
from collections import defaultdict
from hotspot.config import sb_query, sb_upsert, today, week_ago, log


def analyze_trends() -> list[dict]:
    """
    分析最近7天的热点趋势。
    Returns list of trend items: {topic_cluster, trend_type, platforms, mention_count_7d, ...}
    """
    log.info("Analyzing 7-day trends...")

    # 查询最近7天的热点数据
    path = (
        f"content_hotspots?select=topic_cluster,platform,hotspot_score,window_end"
        f"&window_end=gte.{week_ago()}"
        f"&order=window_end.desc"
        f"&limit=500"
    )
    rows = sb_query(path)
    if not rows:
        log.warning("No hotspot data in last 7 days")
        return []

    # 按 topic_cluster 聚合
    clusters = defaultdict(lambda: {
        "platforms": set(),
        "scores": [],
        "daily_counts": defaultdict(int),
        "total_count": 0,
    })

    for row in rows:
        topic = row.get("topic_cluster", "").strip()
        if not topic:
            continue
        c = clusters[topic]
        c["platforms"].add(row.get("platform", ""))
        c["scores"].append(row.get("hotspot_score", 0))
        c["daily_counts"][row.get("window_end", "")] += 1
        c["total_count"] += 1

    # 计算趋势
    trends = []
    for topic, data in clusters.items():
        avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        platform_list = sorted(data["platforms"] - {""})
        mention_count = data["total_count"]

        # 计算日环比变化（简化: 用最近2天 vs 前5天的平均）
        daily = data["daily_counts"]
        dates_sorted = sorted(daily.keys(), reverse=True)

        if len(dates_sorted) >= 3:
            recent_avg = sum(daily[d] for d in dates_sorted[:2]) / 2
            older_avg = sum(daily[d] for d in dates_sorted[2:]) / max(len(dates_sorted) - 2, 1)
            score_delta = recent_avg - older_avg
        else:
            score_delta = 0

        # 趋势分类
        is_cross_platform = len(platform_list) >= 2
        is_new = len(dates_sorted) <= 2

        if is_new and is_cross_platform:
            trend_type = "breakout"
        elif score_delta > 1:
            trend_type = "rising"
        elif score_delta < -1:
            trend_type = "falling"
        else:
            trend_type = "stable"

        trends.append({
            "topic_cluster": topic,
            "trend_type": trend_type,
            "platforms": platform_list,
            "mention_count_7d": mention_count,
            "avg_score_7d": round(avg_score, 1),
            "score_delta": round(score_delta, 2),
            "analysis_date": today(),
            "details": {
                "daily_counts": dict(daily),
                "platform_count": len(platform_list),
            },
        })

    # 按热度排序
    trends.sort(key=lambda t: (
        {"breakout": 4, "rising": 3, "stable": 2, "falling": 1}.get(t["trend_type"], 0),
        t["avg_score_7d"],
    ), reverse=True)

    log.info(f"Analyzed {len(trends)} topic clusters")
    return trends


def save_trends(trends: list[dict]) -> bool:
    """Save trend analysis to Supabase hotspot_trends table."""
    if not trends:
        return True

    rows = []
    for t in trends:
        rows.append({
            "topic_cluster": t["topic_cluster"],
            "trend_type": t["trend_type"],
            "platforms": t["platforms"],
            "mention_count_7d": t["mention_count_7d"],
            "avg_score_7d": t["avg_score_7d"],
            "score_delta": t["score_delta"],
            "analysis_date": t["analysis_date"],
            "details": t["details"],
        })

    return sb_upsert("hotspot_trends", rows, on_conflict="topic_cluster,analysis_date")


def format_trends_summary(trends: list[dict]) -> str:
    """Format trends for TG message."""
    if not trends:
        return ""

    icons = {"rising": "↑", "falling": "↓", "stable": "→", "breakout": "🆕"}
    lines = ["📈 *7日趋势*"]

    for t in trends[:8]:
        icon = icons.get(t["trend_type"], "?")
        platforms_str = f"{len(t['platforms'])}平台"
        delta = f"+{t['score_delta']}" if t["score_delta"] > 0 else str(t["score_delta"])
        lines.append(f"  {icon} {t['topic_cluster']} ({platforms_str} {delta}分)")

    return "\n".join(lines)
