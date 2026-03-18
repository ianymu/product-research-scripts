#!/usr/bin/env python3
"""
morning_health_brief.py — CareShrimp 每日健康早报

每天早上 cron 触发，读取 health_log.json 中最新的 Apple Health 数据，
结合 health_standards.json 专业标准，生成温暖的四维健康早报，推送到 TG。

用法:
  python3 morning_health_brief.py              # 生成并推送
  python3 morning_health_brief.py --dry-run    # 只打印不推送
  python3 morning_health_brief.py --age 30     # 指定年龄

Cron (openclaw.json):
  schedule: "30 7 * * *"  # 每天 07:30 北京时间
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 复用 apple_health_sync 的核心函数
sys.path.insert(0, str(Path(__file__).parent))
from apple_health_sync import (
    load_json,
    load_standards,
    analyze_health,
    is_reminder_allowed,
    HEALTH_LOG_PATH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("morning_health_brief")


def generate_brief(health_log: dict, analysis: dict, user_age: int = 30) -> str:
    """生成温暖的四维健康早报文本"""
    today = datetime.now().strftime("%m月%d日")
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_map[datetime.now().weekday()]

    lines = [f"🦐 *健康虾早报* — {today} {weekday}", ""]

    # --- 睡眠概览 ---
    sleep_hours = health_log.get("sleep_hours_estimated")
    sleep_data = health_log.get("sleep_data", {})

    if sleep_hours:
        emoji = "😴" if sleep_hours >= 7 else "⚠️" if sleep_hours >= 5 else "🚨"
        lines.append(f"{emoji} *睡眠*: {sleep_hours}h")

        details = []
        eff = sleep_data.get("efficiency_pct")
        if eff:
            details.append(f"效率 {eff}%")
        deep = sleep_data.get("deep_pct")
        if deep is not None:
            details.append(f"深度 {deep}%")
        rem = sleep_data.get("rem_pct")
        if rem is not None:
            details.append(f"REM {rem}%")
        latency = sleep_data.get("latency_min")
        if latency is not None:
            details.append(f"入睡 {int(latency)}min")

        if details:
            lines.append(f"  {' | '.join(details)}")
    else:
        lines.append("😴 *睡眠*: 暂无数据")

    lines.append("")

    # --- 心率 ---
    rhr = health_log.get("resting_hr_bpm")
    if rhr:
        emoji = "💚" if 60 <= rhr <= 75 else "💛" if rhr <= 85 else "🔴"
        lines.append(f"{emoji} *静息心率*: {rhr} bpm")
    else:
        lines.append("❤️ *静息心率*: 暂无数据")

    # --- HRV ---
    hrv = health_log.get("hrv_latest_ms")
    hrv_avg = health_log.get("hrv_7day_avg_ms")
    if hrv:
        if hrv_avg and hrv_avg > 0:
            change = hrv - hrv_avg
            change_str = f"{'↑' if change >= 0 else '↓'}{abs(change):.0f}ms vs 7日均值"
        else:
            change_str = ""
        emoji = "💚" if hrv >= 40 else "💛" if hrv >= 20 else "🔴"
        lines.append(f"{emoji} *HRV*: {hrv} ms {change_str}")
    else:
        lines.append("💓 *HRV*: 暂无数据")

    lines.append("")

    # --- 告警 ---
    alerts = analysis.get("alerts", [])
    burnout = analysis.get("burnout", {})

    if alerts:
        critical = [a for a in alerts if a["severity"] == "critical"]
        warnings = [a for a in alerts if a["severity"] == "warning"]

        if critical:
            lines.append("🚨 *需要注意:*")
            for a in critical:
                lines.append(f"  • {a['message']}")
            lines.append("")

        if warnings:
            lines.append("⚠️ *建议关注:*")
            for a in warnings:
                lines.append(f"  • {a['message']}")
            lines.append("")

    # --- 今日建议 ---
    lines.append("📋 *今日建议:*")
    suggestions = _generate_suggestions(health_log, analysis)
    for s in suggestions:
        lines.append(f"  • {s}")

    # --- 倦怠状态 ---
    lines.append("")
    severity = burnout.get("severity", "normal")
    signals = burnout.get("signals_active", 0)
    if severity == "critical":
        lines.append(f"🚨 *倦怠风险: 高* ({signals}/7 信号)")
        lines.append("  强烈建议今天减量，你先休息。")
    elif severity == "warning":
        lines.append(f"⚠️ *倦怠风险: 中* ({signals}/7 信号)")
        lines.append("  建议减负 30%，优先恢复。")
    elif severity == "monitor":
        lines.append(f"👀 *身体状态: 需留意* ({signals}/7 信号)")
    else:
        lines.append("✅ *身体状态: 良好*")

    lines.append("")
    lines.append("_— 健康虾，守护你的每一天 🦐_")

    return "\n".join(lines)


def _generate_suggestions(health_log: dict, analysis: dict) -> list:
    """基于数据生成个性化建议"""
    suggestions = []
    sleep_hours = health_log.get("sleep_hours_estimated", 0)
    sleep_data = health_log.get("sleep_data", {})
    rhr = health_log.get("resting_hr_bpm")
    hrv = health_log.get("hrv_latest_ms")
    burnout = analysis.get("burnout", {})

    # 睡眠不足 → 补觉建议
    if sleep_hours and sleep_hours < 7:
        deficit = round(7 - sleep_hours, 1)
        suggestions.append(f"昨晚少睡了 {deficit}h，中午争取补个 20-30 分钟午休")

    # 深度睡眠不足 → 睡前建议
    deep_pct = sleep_data.get("deep_pct")
    if deep_pct is not None and deep_pct < 10:
        suggestions.append("深度睡眠不足，今晚试试：睡前 1h 不看屏幕、保持房间凉爽(18-20°C)")

    # 入睡困难
    latency = sleep_data.get("latency_min")
    if latency and latency > 30:
        suggestions.append("入睡时间偏长，试试 4-7-8 呼吸法（吸4秒-屏7秒-呼8秒）")

    # 心率偏高
    if rhr and rhr > 80:
        suggestions.append("心率偏高，注意压力管理，多做几次深呼吸")

    # HRV 偏低
    if hrv and hrv < 30:
        suggestions.append("HRV 偏低，身体恢复状态不好，今天避免高强度工作")

    # 倦怠状态
    severity = burnout.get("severity", "normal")
    if severity in ("warning", "critical"):
        suggestions.append("倦怠信号明显，今天的工作量建议减少 30%")
        suggestions.append("每工作 50 分钟就站起来走 5 分钟")

    # 通用建议（如果没有特别问题）
    if not suggestions:
        suggestions.append("状态不错！保持规律作息，记得喝水 💧")
        suggestions.append("每工作 2 小时起来活动 5 分钟")

    # 永远加饮水
    if "喝水" not in " ".join(suggestions) and "饮水" not in " ".join(suggestions):
        suggestions.append("别忘了喝水，目标 8 杯 💧")

    return suggestions[:5]  # 最多 5 条建议


def send_tg(message: str):
    """发送到 Telegram"""
    token = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("TG 未配置 (TG_SHRIMPILOT_TOKEN / TG_SHRIMPILOT_CHAT_ID)")
        return False

    import urllib.request
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    ).encode()
    try:
        urllib.request.urlopen(url, data, timeout=15)
        log.info("TG 早报已推送")
        return True
    except Exception as e:
        log.error("TG 推送失败: %s", e)
        return False


def main():
    parser = argparse.ArgumentParser(description="CareShrimp 每日健康早报")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送 TG")
    parser.add_argument("--age", type=int, default=30, help="用户年龄 (默认 30)")
    args = parser.parse_args()

    # 检查是否允许发送提醒
    if not args.dry_run and not is_reminder_allowed("sleep"):
        log.info("当前处于静音状态，跳过早报推送")
        return

    # 读取健康数据
    health_log = load_json(HEALTH_LOG_PATH, {})
    if not health_log:
        log.warning("health_log.json 为空，使用默认数据")
        health_log = {"date": datetime.now().strftime("%Y-%m-%d")}

    # 分析
    analysis = analyze_health(health_log, user_age=args.age)

    # 生成早报
    brief = generate_brief(health_log, analysis, user_age=args.age)

    if args.dry_run:
        print(brief)
        print("\n[dry-run] 未推送 TG")
    else:
        print(brief)
        send_tg(brief)


if __name__ == "__main__":
    main()
