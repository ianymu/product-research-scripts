#!/usr/bin/env python3
"""
ops_tracker/daily_report.py — 每日/7日运营报表 + TG 推送
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, log


def _pct_change(current: int, previous: int) -> str:
    if previous == 0:
        return "+∞" if current > 0 else "0%"
    pct = ((current - previous) / previous) * 100
    return f"+{pct:.0f}%" if pct >= 0 else f"{pct:.0f}%"


def get_daily_stats(date_str: str = None) -> list[dict]:
    """Get daily stats from Supabase."""
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    return sb_query(
        f"ops_daily_stats?select=*&stat_date=eq.{date_str}&order=platform"
    )


def get_7day_stats() -> dict:
    """Get 7-day aggregated stats per platform."""
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = sb_query(
        f"ops_daily_stats?select=*&stat_date=gte.{week_ago}&order=stat_date"
    )

    # Aggregate by platform
    by_platform = {}
    for row in rows:
        p = row.get("platform", "unknown")
        if p not in by_platform:
            by_platform[p] = {
                "articles": 0, "reads": 0, "likes": 0,
                "shares": 0, "comments": 0, "saves": 0, "days": 0,
            }
        bp = by_platform[p]
        bp["articles"] += row.get("articles_count", 0)
        bp["reads"] += row.get("total_reads", 0)
        bp["likes"] += row.get("total_likes", 0)
        bp["shares"] += row.get("total_shares", 0)
        bp["comments"] += row.get("total_comments", 0)
        bp["saves"] += row.get("total_saves", 0)
        bp["days"] += 1

    return by_platform


def generate_daily_report(date_str: str = None) -> str:
    """Generate formatted daily ops report for TG."""
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_stats = get_daily_stats(date_str)
    prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_stats = get_daily_stats(prev_date)

    # Build prev lookup
    prev_map = {s.get("platform"): s for s in prev_stats}

    lines = [f"📈 *运营日报* — {date_str}", ""]

    platform_names = {"wechat": "微信", "xhs": "小红书", "x": "X"}
    for stat in today_stats:
        p = stat.get("platform", "")
        name = platform_names.get(p, p)
        prev = prev_map.get(p, {})

        articles = stat.get("articles_count", 0)
        reads = stat.get("total_reads", 0)
        likes = stat.get("total_likes", 0)
        shares = stat.get("total_shares", 0)
        comments = stat.get("total_comments", 0)
        saves = stat.get("total_saves", 0)

        reads_change = _pct_change(reads, prev.get("total_reads", 0))
        likes_change = _pct_change(likes, prev.get("total_likes", 0))

        line = f"*{name}*: {articles} 篇"
        if reads > 0:
            line += f" | 阅读 {reads:,} ({reads_change})"
        if likes > 0:
            line += f" | 赞 {likes}"
        if shares > 0:
            line += f" | 转发 {shares}"
        if comments > 0:
            line += f" | 评论 {comments}"
        if saves > 0:
            line += f" | 收藏 {saves}"
        lines.append(line)

    # 7-day trends
    week_data = get_7day_stats()
    if week_data:
        lines.append("")
        lines.append("*7日趋势*:")
        for p, data in week_data.items():
            name = platform_names.get(p, p)
            avg_reads = data["reads"] // max(data["days"], 1)
            lines.append(f"  {name}: 共 {data['articles']} 篇, 日均阅读 {avg_reads:,}")

    return "\n".join(lines)


def generate_optimization_suggestions(date_str: str = None) -> str:
    """Generate optimization suggestions based on data patterns."""
    week_data = get_7day_stats()
    if not week_data:
        return ""

    suggestions = ["", "💡 *优化建议*:"]

    # Check for underperforming platforms
    for p, data in week_data.items():
        name = {"wechat": "微信", "xhs": "小红书", "x": "X"}.get(p, p)
        if data["articles"] == 0:
            suggestions.append(f"  - {name}: 本周无发布，建议至少每周 3 篇")
        elif data["reads"] > 0 and data["likes"] / max(data["reads"], 1) < 0.01:
            suggestions.append(f"  - {name}: 点赞率低于 1%，建议优化标题和开头 hook")
        if data["comments"] == 0 and data["articles"] > 0:
            suggestions.append(f"  - {name}: 零评论，建议文末加互动问题")

    return "\n".join(suggestions) if len(suggestions) > 2 else ""
