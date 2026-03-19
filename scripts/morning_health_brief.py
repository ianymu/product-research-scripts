#!/usr/bin/env python3
"""
morning_health_brief.py — CareShrimp 每日健康早报 V3 (3 条消息)

拆分为 3 条 TG 消息:
  1. 睡眠 + 身体状态
  2. 今日建议 (引用 WHO/NIH/AASM 标准)
  3. 饮食建议 (结合一周数据 AI 分析)

用法:
  python3 morning_health_brief.py              # 生成并推送
  python3 morning_health_brief.py --dry-run    # 只打印不推送
  python3 morning_health_brief.py --age 30     # 指定年龄

Cron (openclaw.json):
  schedule: "0 23 * * *"  # UTC 23:00 = CST 07:00
"""

import os
import sys
import json
import logging
import argparse
import time as _time
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

WEB_URL = "http://18.221.160.170/shrimp"
STANDARDS_PATH = Path(__file__).parent / "health_standards.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _week_avg(daily_history: list, key: str, sub_key: str = None, days: int = 7) -> float:
    """Calculate 7-day average for a metric from daily_history."""
    recent = daily_history[-days:] if daily_history else []
    vals = []
    for d in recent:
        if sub_key:
            v = d.get(key, {}).get(sub_key)
        else:
            v = d.get(key)
        if v is not None:
            vals.append(v)
    return round(sum(vals) / len(vals), 1) if vals else None


def _burnout_signals_count(health_log: dict, analysis: dict) -> int:
    """Count active burnout signals."""
    burnout = analysis.get("burnout", {})
    return burnout.get("signals_active", 0)


# ---------------------------------------------------------------------------
# Message 1 — 睡眠 + 身体状态
# ---------------------------------------------------------------------------

def generate_msg1(health_log: dict, analysis: dict) -> str:
    today = datetime.now().strftime("%-m月%-d日")

    lines = [f"🦞 *健康虾早报* — {today}", ""]

    sleep_hours = health_log.get("sleep_hours_estimated") or health_log.get("sleep_data", {}).get("total_hours")
    sleep_data = health_log.get("sleep_data", {})

    # --- 睡眠总览 ---
    if sleep_hours:
        eff = sleep_data.get("efficiency_pct")
        eff_str = f" (效率 {eff}%)" if eff else ""
        emoji = "😴" if sleep_hours >= 7 else "⚠️" if sleep_hours >= 5 else "🚨"
        lines.append(f"{emoji} *睡眠 {sleep_hours}h{eff_str}*")

        # 深度睡眠
        deep_pct = sleep_data.get("deep_pct")
        if deep_pct is not None:
            deep_h = round(sleep_hours * deep_pct / 100, 1)
            deep_emoji = "✅" if deep_pct >= 15 else "⚠️"
            lines.append(f"  💤 深度 {deep_h}h ({deep_pct}%) {deep_emoji}")

        # REM
        rem_pct = sleep_data.get("rem_pct")
        if rem_pct is not None:
            rem_h = round(sleep_hours * rem_pct / 100, 1)
            rem_emoji = "✅" if rem_pct >= 20 else "⚠️"
            lines.append(f"  🧠 REM {rem_h}h ({rem_pct}%) {rem_emoji}")

        # 入睡时间
        latency = sleep_data.get("latency_min")
        if latency is not None:
            lat_emoji = "✅" if latency <= 15 else "⚠️" if latency <= 30 else "🚨"
            lines.append(f"  ⏱ 入睡 {int(latency)}min {lat_emoji}")
    else:
        lines.append("😴 *睡眠*: 暂无数据")

    lines.append("")

    # --- 心率 + HRV ---
    rhr = health_log.get("resting_hr_bpm")
    hrv = health_log.get("hrv_latest_ms")
    hrv_history = health_log.get("hrv_history", [])

    parts = []
    if rhr:
        emoji = "💚" if 60 <= rhr <= 75 else "💛" if rhr <= 85 else "🔴"
        parts.append(f"{emoji} 静息心率 {rhr}bpm")
    if hrv:
        # 计算 7 日均值差
        recent_hrv = [h.get("avg", 0) for h in hrv_history[-7:]] if hrv_history else []
        avg_7d = sum(recent_hrv) / len(recent_hrv) if recent_hrv else 0
        change = hrv - avg_7d if avg_7d > 0 else 0
        change_str = f" ({'↑' if change >= 0 else '↓'}{abs(change):.0f}ms)" if avg_7d > 0 else ""
        parts.append(f"HRV {hrv}ms{change_str}")
    if parts:
        lines.append(" | ".join(parts))

    # --- 身体状态 ---
    signals = _burnout_signals_count(health_log, analysis)
    burnout = analysis.get("burnout", {})
    severity = burnout.get("severity", "normal")
    if severity == "critical":
        lines.append(f"🚨 身体状态: 需立即休息 ({signals}/7 信号)")
    elif severity == "warning":
        lines.append(f"⚠️ 身体状态: 需关注 ({signals}/7 信号)")
    elif severity == "monitor":
        lines.append(f"👀 身体状态: 需留意 ({signals}/7 信号)")
    else:
        lines.append(f"✅ 身体状态: 良好 ({signals}/7 信号)")

    lines.append("")
    lines.append(f"🔗 [详细趋势图]({WEB_URL}/health#overview)")
    lines.append("_— 健康虾，守护你的每一天 🦞_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Message 2 — 今日建议 (引用 health_standards.json)
# ---------------------------------------------------------------------------

def generate_msg2(health_log: dict, analysis: dict, standards: dict) -> str:
    lines = ["📋 *今日建议*", ""]

    sleep_hours = health_log.get("sleep_hours_estimated") or health_log.get("sleep_data", {}).get("total_hours", 0)
    sleep_data = health_log.get("sleep_data", {})
    hrv = health_log.get("hrv_latest_ms")
    rhr = health_log.get("resting_hr_bpm")

    suggestions = []

    # 深度睡眠
    deep_pct = sleep_data.get("deep_pct")
    std_sleep = standards.get("sleep", {})
    std_deep = std_sleep.get("architecture_pct", {}).get("N3_deep", {})
    deep_min = std_deep.get("normal_min", 10)
    deep_max = std_deep.get("normal_max", 20)
    if deep_pct is not None and deep_pct < deep_min + 5:  # AASM recommends 15-25%
        suggestions.append(
            f"• 深度睡眠 {deep_pct}%，低于 AASM 建议的 {deep_min+5}-{deep_max+5}%"
            f"（来源: American Academy of Sleep Medicine）\n"
            f"  → 今晚：睡前 1h 放下手机 + 房间温度 18-20°C"
        )

    # HRV
    std_hrv = standards.get("hrv", {}).get("by_age", {}).get("26_35", {})
    hrv_low = std_hrv.get("low_p25", 40)
    hrv_avg = std_hrv.get("avg_p50", 60)
    if hrv and hrv < hrv_avg:
        suggestions.append(
            f"• HRV {hrv}ms，NIH 标准 {hrv_low}-{hrv_avg}ms 区间"
            f"{'偏低' if hrv < hrv_low else '中等'}\n"
            f"  → 午后做 10min 正念呼吸可提升 HRV 15-20%"
        )

    # 睡眠时长
    std_dur = std_sleep.get("recommended_duration_hours", {}).get("adults_18_64", {})
    sleep_min = std_dur.get("min", 7)
    if sleep_hours and sleep_hours < sleep_min:
        deficit = round(sleep_min - sleep_hours, 1)
        suggestions.append(
            f"• 昨晚睡了 {sleep_hours}h，低于 NIH 建议的 {sleep_min}h（缺 {deficit}h）\n"
            f"  → 午间补个 20min 午睡可恢复 60% 精力"
        )

    # 入睡时间
    latency = sleep_data.get("latency_min")
    std_lat = std_sleep.get("latency_minutes", {})
    lat_border = std_lat.get("borderline_max", 30)
    if latency and latency > 20:
        suggestions.append(
            f"• 入睡耗时 {int(latency)}min，Sleep Foundation 建议 10-20min\n"
            f"  → 试试 4-7-8 呼吸法：吸 4 秒 → 屏 7 秒 → 呼 8 秒"
        )

    # 心率
    if rhr and rhr > 80:
        suggestions.append(
            f"• 静息心率 {rhr}bpm，高于 Mayo Clinic 建议的 60-75bpm\n"
            f"  → 每 2h 做 3 次深呼吸，降低交感神经激活"
        )

    # 饮水（通用）
    suggestions.append("• 全天饮水目标 2000ml，每 2h 一杯")

    if not suggestions:
        suggestions.append("• 各项指标良好！保持规律作息")

    lines.extend(suggestions)

    lines.append("")
    lines.append(f"🔗 [更多专业分析]({WEB_URL}/health#suggestions)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Message 3 — 饮食建议 (结合一周数据)
# ---------------------------------------------------------------------------

def generate_msg3(health_log: dict, analysis: dict) -> str:
    lines = ["🍽 *饮食建议*（基于本周身体数据 AI 分析）", ""]

    sleep_hours = health_log.get("sleep_hours_estimated") or health_log.get("sleep_data", {}).get("total_hours", 0)
    hrv = health_log.get("hrv_latest_ms")
    daily_history = health_log.get("daily_history", [])

    # 计算一周均值
    week_sleep = _week_avg(daily_history, "sleep_hours_estimated") or _week_avg(daily_history, "sleep_data", "total_hours")
    week_hrv_vals = [h.get("hrv_latest_ms") for h in daily_history[-7:] if h.get("hrv_latest_ms")]
    week_hrv_trend = "偏低" if week_hrv_vals and sum(week_hrv_vals)/len(week_hrv_vals) < 35 else "正常"

    week_sleep_str = f"{week_sleep}h" if week_sleep else "数据不足"

    lines.append(f"本周睡眠均值 {week_sleep_str} | HRV 趋势{week_hrv_trend} → ", )

    # 判断饮食策略
    needs_recovery = (sleep_hours and sleep_hours < 7) or (hrv and hrv < 35) or week_hrv_trend == "偏低"

    if needs_recovery:
        lines[-1] += "恢复型饮食"
        lines.append("")
        lines.append("*午餐*")
        lines.append("• 三文鱼/鸡胸肉 + 西兰花/菠菜（富含镁，改善深度睡眠 — Mayo Clinic）")
        lines.append("• 避免重油炒饭/面食（血糖 spike → 下午犯困）")
        lines.append("")
        lines.append("*晚餐（助眠型）*")
        lines.append("• 含色氨酸：火鸡/香蕉/温牛奶")
        lines.append("• 含 GABA：糙米/番茄")
        lines.append("• 睡前 2h 不吃重食")
    else:
        lines[-1] += "均衡型饮食"
        lines.append("")
        lines.append("*午餐*")
        lines.append("• 均衡搭配：蛋白质 + 蔬菜 + 适量碳水")
        lines.append("• 餐后散步 10min，帮助消化 + 稳定血糖")
        lines.append("")
        lines.append("*晚餐*")
        lines.append("• 清淡为主，距睡觉 3h 前吃完")
        lines.append("• 下午加餐：一份水果 + 坚果补充能量")

    lines.append("")
    lines.append("📸 中午记得给我拍照，我好给你更多建议！")
    lines.append("")
    lines.append(f"🔗 [完整饮食分析]({WEB_URL}/health#diet)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TG sender
# ---------------------------------------------------------------------------

def send_tg(message: str) -> bool:
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
        resp = urllib.request.urlopen(url, data, timeout=15)
        result = json.loads(resp.read().decode())
        if not result.get("ok"):
            # Retry without Markdown
            data2 = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": message}
            ).encode()
            urllib.request.urlopen(url, data2, timeout=15)
        log.info("TG 消息已推送")
        return True
    except Exception as e:
        log.error("TG 推送失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CareShrimp 每日健康早报 V3 (3条消息)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送 TG")
    parser.add_argument("--age", type=int, default=30, help="用户年龄 (默认 30)")
    args = parser.parse_args()

    # 检查是否允许发送提醒
    if not args.dry_run and not is_reminder_allowed("sleep"):
        log.info("当前处于静音状态，跳过早报推送")
        return

    # 读取数据
    health_log = load_json(HEALTH_LOG_PATH, {})
    if not health_log:
        log.warning("health_log.json 为空，使用默认数据")
        health_log = {"date": datetime.now().strftime("%Y-%m-%d")}

    standards = load_json(STANDARDS_PATH, {})
    analysis = analyze_health(health_log, user_age=args.age)

    # 生成 3 条消息
    msg1 = generate_msg1(health_log, analysis)
    msg2 = generate_msg2(health_log, analysis, standards)
    msg3 = generate_msg3(health_log, analysis)

    if args.dry_run:
        print("=" * 50)
        print("[消息 1 — 睡眠 + 身体状态]")
        print("=" * 50)
        print(msg1)
        print()
        print("=" * 50)
        print("[消息 2 — 今日建议]")
        print("=" * 50)
        print(msg2)
        print()
        print("=" * 50)
        print("[消息 3 — 饮食建议]")
        print("=" * 50)
        print(msg3)
        print("\n[dry-run] 未推送 TG")
    else:
        log.info("发送 3 条早报消息...")
        send_tg(msg1)
        _time.sleep(2)
        send_tg(msg2)
        _time.sleep(2)
        send_tg(msg3)
        log.info("3 条早报消息发送完毕")


if __name__ == "__main__":
    main()
