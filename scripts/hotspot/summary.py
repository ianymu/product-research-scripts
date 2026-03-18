#!/usr/bin/env python3
"""
hotspot/summary.py — 增强版 TG 摘要
包含: 发文时间 + 趋势 + YouTube匹配 + 评论待回复
"""
import json
from datetime import datetime
from collections import Counter
from hotspot.config import log


def generate_hotspot_summary(
    items: list[dict],
    trends: list[dict] = None,
    youtube_matches: list[dict] = None,
    incremental: bool = True,
) -> str:
    """Generate formatted TG summary with all sections."""

    mode = "增量" if incremental else "7日"
    lines = [f"📊 *每日热点监测* — {datetime.now().strftime('%Y-%m-%d')} ({mode})", ""]

    # === Per-platform breakdown ===
    by_platform = {}
    for item in items:
        p = item.get("platform", "unknown")
        by_platform.setdefault(p, []).append(item)

    platform_meta = {
        "wechat": ("微信公众号", 11),
        "xhs": ("小红书", 10),
        "x": ("X/Twitter", 13),
    }

    for platform, (name, account_count) in platform_meta.items():
        plist = by_platform.get(platform, [])
        if not plist:
            continue

        plist.sort(key=lambda x: x.get("hotspot_score", 0), reverse=True)
        lines.append(f"*{name}* ({account_count} 账号, {len(plist)} 条新)")

        seen_topics = set()
        count = 0
        for item in plist:
            topic = item.get("topic_cluster") or item.get("title", "")
            if topic in seen_topics:
                continue
            seen_topics.add(topic)

            score = item.get("hotspot_score", 0)
            source = item.get("source_name", "")
            post_time = item.get("estimated_post_time", "")
            time_str = f" [{post_time}]" if post_time and post_time != "未知" else ""

            # Trend indicator from trend data
            trend_icon = ""
            if trends:
                for t in trends:
                    if t["topic_cluster"] == topic:
                        trend_icon = {"rising": " ↑", "falling": " ↓", "breakout": " 🆕"}.get(t["trend_type"], "")
                        break

            lines.append(f"  {count+1}. {topic} (🔥{score}{trend_icon}) @{source}{time_str}")
            count += 1
            if count >= 5:
                break
        lines.append("")

    # === Trends section ===
    if trends:
        from hotspot.trend_analyzer import format_trends_summary
        lines.append(format_trends_summary(trends))
        lines.append("")

    # === YouTube matches section ===
    if youtube_matches:
        lines.append("📺 *YouTube 素材匹配*")
        for i, m in enumerate(youtube_matches[:3]):
            topic = m.get("hotspot_topic", "")
            title = m.get("youtube_title", "")
            score = m.get("match_score", 0)
            lines.append(f"  {i+1}. [{topic}] → \"{title}\" (匹配度{score:.0%})")
            if m.get("suggestion"):
                lines.append(f"     💡 {m['suggestion']}")
        lines.append("")

    # === Cross-platform keywords ===
    all_keywords = []
    for item in items:
        all_keywords.extend(item.get("keywords", []))
    top_cross = Counter(all_keywords).most_common(5)
    if top_cross:
        lines.append("*跨平台高频词*:")
        lines.append("  " + " | ".join(f"{w}({c})" for w, c in top_cross))
        lines.append("")

    return "\n".join(lines)
