#!/usr/bin/env python3
"""
care_evening_checkin.py — CareShrimp 22:00 情绪 Check-in
每晚推送互动消息询问今天感受，记录到 health_log.json，基于历史数据给个性化建议。
"""
import os
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
MEMORY_DIR = Path(os.environ.get("SHRIMPILOT_MEMORY", os.path.expanduser("~/.shrimpilot/memory")))
HEALTH_LOG = MEMORY_DIR / "health_log.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("care_evening")


# ── Health Log I/O ──────────────────────────────────────────
def read_health_log() -> dict:
    if HEALTH_LOG.exists():
        try:
            return json.loads(HEALTH_LOG.read_text())
        except (json.JSONDecodeError, IOError):
            log.warning("health_log.json 损坏，使用空数据")
    return {"daily_records": [], "checkins": []}


def write_health_log(data: dict):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info(f"已写入 {HEALTH_LOG}")


# ── Analyze Today ───────────────────────────────────────────
def analyze_today(health_data: dict) -> dict:
    """从 health_log 分析今日状态."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    records = health_data.get("daily_records", [])

    # 找今日记录
    today_record = None
    for r in records:
        if r.get("date", "").startswith(today_str):
            today_record = r
            break

    # 最近 7 天 checkin 情绪趋势
    checkins = health_data.get("checkins", [])
    recent_moods = []
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    for c in checkins:
        if c.get("timestamp", "") >= week_ago:
            recent_moods.append(c.get("mood_score", 5))

    return {
        "has_health_data": today_record is not None,
        "sleep_hours": today_record.get("sleep_hours", 0) if today_record else 0,
        "fatigue_level": today_record.get("fatigue_level", "unknown") if today_record else "unknown",
        "burnout_signals": today_record.get("burnout_signals", 0) if today_record else 0,
        "recent_mood_avg": sum(recent_moods) / len(recent_moods) if recent_moods else 0,
        "mood_trend_days": len(recent_moods),
    }


# ── Generate Check-in Message ──────────────────────────────
def generate_checkin_message(analysis: dict) -> str:
    now = datetime.now()
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    date_str = now.strftime(f"%m月%d日 周{weekday}")

    msg_parts = [f"*健康虾晚间 Check-in* — {date_str}\n"]

    # 今日回顾
    if analysis["has_health_data"]:
        sleep = analysis["sleep_hours"]
        fatigue = analysis["fatigue_level"]
        signals = analysis["burnout_signals"]

        msg_parts.append("*今日回顾:*")
        if sleep > 0:
            emoji = "" if sleep >= 7 else "" if sleep >= 6 else ""
            msg_parts.append(f"  {emoji} 昨晚睡眠: {sleep:.1f}h")
        if fatigue != "unknown":
            fatigue_map = {"normal": "正常", "monitor": "需关注", "warning": "警告", "critical": "严重"}
            msg_parts.append(f"  疲劳状态: {fatigue_map.get(fatigue, fatigue)}")
        if signals >= 3:
            msg_parts.append(f"  倦怠信号: {signals}/7 — 请认真对待")
        msg_parts.append("")

    # 情绪趋势
    if analysis["mood_trend_days"] > 0:
        avg = analysis["recent_mood_avg"]
        trend_emoji = "" if avg >= 7 else "" if avg >= 5 else ""
        msg_parts.append(f"*近{analysis['mood_trend_days']}天情绪均值:* {avg:.1f}/10 {trend_emoji}")
        if avg < 5:
            msg_parts.append("  连续低情绪，建议明天安排轻松的工作")
        msg_parts.append("")

    # 互动问题
    msg_parts.append("*今天过得怎么样？*")
    msg_parts.append("回复一个数字 (1-10)：")
    msg_parts.append("  1-3: 很糟糕  4-5: 一般般  6-7: 还不错  8-10: 很棒")
    msg_parts.append("")

    # 个性化建议
    msg_parts.append("*晚间建议:*")
    hour = now.hour
    suggestions = []

    if analysis.get("fatigue_level") in ("warning", "critical"):
        suggestions.append("今天身体发出了警告，早点休息，明天的事明天再说")
    if hour >= 22:
        suggestions.append("现在开始远离屏幕蓝光，帮助入睡")
    if analysis.get("sleep_hours", 0) > 0 and analysis["sleep_hours"] < 6:
        suggestions.append("昨晚睡眠不足，今晚争取 11 点前上床")

    # 默认建议
    if not suggestions:
        suggestions = [
            "回顾今天做得最好的一件事，给自己点个赞",
            "睡前 30 分钟放下手机，读几页书或冥想",
            "明天的事明天再想，现在属于你自己",
        ]

    for s in suggestions:
        msg_parts.append(f"  - {s}")

    msg_parts.append("\n_— 健康虾，守护你的每一天_")

    return "\n".join(msg_parts)


# ── Record Check-in ────────────────────────────────────────
def record_checkin(health_data: dict, mood_score: int = 0, note: str = "") -> dict:
    """记录一次 check-in 到 health_log."""
    checkin = {
        "timestamp": datetime.now().isoformat(),
        "type": "evening",
        "mood_score": mood_score,
        "note": note,
    }
    if "checkins" not in health_data:
        health_data["checkins"] = []
    health_data["checkins"].append(checkin)

    # 只保留最近 90 天
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    health_data["checkins"] = [c for c in health_data["checkins"] if c.get("timestamp", "") >= cutoff]

    return health_data


# ── TG Push ─────────────────────────────────────────────────
def send_tg(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.error("TG_BOT_TOKEN 或 TG_CHAT_ID 未设置")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = httpx.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=15)
    if resp.status_code == 200:
        log.info("TG 推送成功")
    else:
        log.error(f"TG 推送失败: {resp.status_code} {resp.text}")


# ── Main ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CareShrimp 晚间情绪 Check-in")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送 TG")
    parser.add_argument("--record", type=int, help="记录情绪分数 (1-10)")
    parser.add_argument("--note", type=str, default="", help="附加备注")
    args = parser.parse_args()

    health_data = read_health_log()

    if args.record:
        # 记录模式：用户回复了情绪分数
        score = max(1, min(10, args.record))
        health_data = record_checkin(health_data, mood_score=score, note=args.note)
        write_health_log(health_data)
        log.info(f"已记录情绪分数: {score}/10")
        return

    # 推送模式：发送晚间 check-in
    analysis = analyze_today(health_data)
    message = generate_checkin_message(analysis)

    if args.dry_run:
        print(message)
        print("\n[dry-run] 未推送 TG")
    else:
        send_tg(message)
        # 记录一次 check-in 发送事件（无分数，等用户回复）
        health_data = record_checkin(health_data, mood_score=0, note="check-in sent, awaiting reply")
        write_health_log(health_data)


if __name__ == "__main__":
    main()
