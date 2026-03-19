"""
hotspot_monitor TG 输出补丁

将此函数替换 hotspot_monitor.py 中 main() 末尾的摘要推送逻辑。
TG 只发精简版 Top 3 + 链接，完整数据看网站。

用法: 将 generate_compact_tg_summary() 集成到 hotspot_monitor.py
"""

WEB_URL = "http://18.221.160.170/shrimp"


def generate_compact_tg_summary(all_items, trends=None):
    """生成精简版 TG 热点摘要（Top 3 + 网站链接）"""
    from datetime import datetime

    today = datetime.now().strftime("%m/%d")

    # 按热度排序取 Top 3
    sorted_items = sorted(all_items, key=lambda x: x.get("hotspot_score", 0), reverse=True)
    top3 = sorted_items[:3]

    # 平台统计
    by_platform = {}
    for item in all_items:
        p = item.get("platform", "unknown")
        by_platform[p] = by_platform.get(p, 0) + 1

    lines = [
        f"📊 *今日热点简报* — {today}",
        "",
        "🔥 *跨平台 Top 3 热点:*",
    ]

    for i, item in enumerate(top3):
        topic = item.get("topic_cluster") or item.get("title", "未分类")
        score = item.get("hotspot_score", 0)
        lines.append(f"{i+1}. {topic} (🔥{score})")

    lines.append("")

    # 平台统计
    wc = by_platform.get("wechat", 0)
    xhs = by_platform.get("xhs", 0)
    x = by_platform.get("x", 0)
    lines.append(f"📱 三平台新增：微信 {wc} 条 | 小红书 {xhs} 条 | X {x} 条")

    # 趋势
    if trends:
        rising = [t for t in trends if t.get("trend_type") in ("rising", "breakout")]
        if rising:
            topics = "、".join(t.get("topic_cluster", "")[:10] for t in rising[:3])
            lines.append(f"📈 7 日趋势：{topics} 持续升温")

    lines.append("")
    lines.append(f"🔗 详情 → {WEB_URL}/hotspot")
    lines.append("")
    lines.append("_— 运营虾 🦐_")

    return "\n".join(lines)
