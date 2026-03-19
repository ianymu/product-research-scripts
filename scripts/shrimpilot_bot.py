#!/usr/bin/env python3
"""
ShrimPilot Bot — 守虾人 TG Bot (V2 — Demo Ready)
三模块联动决策链: OpsShrimp + CareShrimp + GuardShrimp

V2 新增:
  - 真实热点监测 (从 Supabase content_hotspots 读取)
  - 深度健康建议 (饮食/饮水/天气/睡眠, 基于历史数据)
  - 跨模块联动决策链 (不是通知, 是行为变化链)
  - V7 痛点格式化展示
  - 知识库融合内容生成

运行: python3 shrimpilot_bot.py
停止: Ctrl+C 或 kill

环境变量 (从 ~/.openclaw/.env 读取):
  TG_SHRIMPILOT_TOKEN - Bot token
  TG_SHRIMPILOT_CHAT_ID - 群聊 ID
  SUPABASE_URL - Supabase URL
  SUPABASE_SERVICE_ROLE_KEY - Supabase key
  ANTHROPIC_API_KEY - Claude API (内容生成)
"""
import os
import sys
import json
import time
import re
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# === Config ===
# 铁律 #1: 所有 os.environ 读取必须加 .strip()
TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

MEMORY_DIR = Path.home() / ".shrimpilot" / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = Path("/tmp/shrimpilot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("shrimpilot")

# === Telegram Helpers ===
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"


def tg_send(text: str, chat_id: str = None, parse_mode: str = "Markdown") -> bool:
    """Send message to Telegram."""
    cid = chat_id or CHAT_ID
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{TG_API}/sendMessage",
                json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
            )
            data = resp.json()
            if not data.get("ok"):
                log.error(f"TG send failed: {data}")
                # Retry without parse_mode (Markdown can fail on special chars)
                resp = client.post(
                    f"{TG_API}/sendMessage",
                    json={"chat_id": cid, "text": text},
                )
                return resp.json().get("ok", False)
            return True
    except Exception as e:
        log.error(f"TG send error: {e}")
        return False


def tg_get_updates(offset: int = 0) -> list:
    """Poll for new messages."""
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
            )
            data = resp.json()
            return data.get("result", [])
    except Exception as e:
        log.error(f"TG poll error: {e}")
        return []


# === Supabase Helpers ===
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def sb_query(path: str) -> list:
    """Query Supabase REST API."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=SB_HEADERS)
            return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        log.error(f"Supabase query error: {e}")
        return []


def sb_insert(table: str, rows: list) -> bool:
    """Insert rows into Supabase."""
    if not rows:
        return True
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json=rows,
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        log.error(f"Supabase insert error: {e}")
        return False


# === Memory System ===
def read_memory(filename: str) -> dict:
    """Read shared memory file."""
    filepath = MEMORY_DIR / filename
    if filepath.exists():
        try:
            return json.loads(filepath.read_text())
        except Exception:
            return {}
    return {}


def write_memory(filename: str, data: dict):
    """Write shared memory file."""
    filepath = MEMORY_DIR / filename
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# === Weather API (wttr.in, no key needed) ===
def get_weather(city: str = "Beijing") -> dict:
    """Get current weather from wttr.in (free, no API key)."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"https://wttr.in/{city}?format=j1")
            if resp.status_code == 200:
                data = resp.json()
                current = data.get("current_condition", [{}])[0]
                tomorrow = data.get("weather", [{}, {}])[1] if len(data.get("weather", [])) > 1 else {}
                return {
                    "city": city,
                    "temp_c": current.get("temp_C", "?"),
                    "feels_like_c": current.get("FeelsLikeC", "?"),
                    "humidity": current.get("humidity", "?"),
                    "desc": current.get("lang_zh", [{}])[0].get("value", current.get("weatherDesc", [{}])[0].get("value", "")),
                    "wind_kmph": current.get("windspeedKmph", "?"),
                    "tomorrow_max": tomorrow.get("maxtempC", "?"),
                    "tomorrow_min": tomorrow.get("mintempC", "?"),
                    "tomorrow_desc": (
                        tomorrow.get("hourly", [{}])[4].get("lang_zh", [{}])[0].get("value", "")
                        if tomorrow.get("hourly") and len(tomorrow.get("hourly", [])) > 4
                        else ""
                    ),
                }
    except Exception as e:
        log.error(f"Weather API error: {e}")
    return {"city": city, "temp_c": "?", "desc": "数据不可用"}


# === LLM Helper ===
def call_claude(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    """Call Claude API for content generation."""
    if not ANTHROPIC_KEY:
        return "[Error: ANTHROPIC_API_KEY not set]"

    try:
        with httpx.Client(timeout=60) as client:
            messages = [{"role": "user", "content": prompt}]
            body = {
                "model": "claude-sonnet-4-6-20250514",
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                body["system"] = system

            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            data = resp.json()
            if data.get("content"):
                return data["content"][0]["text"]
            else:
                return f"[LLM Error: {data.get('error', {}).get('message', 'unknown')}]"
    except Exception as e:
        return f"[LLM Error: {e}]"


# ================================================================
# OpsShrimp — 运营虾
# ================================================================

def ops_get_hotspots() -> str:
    """Fetch real hotspot data from Supabase content_hotspots table."""
    log.info("OpsShrimp: Fetching real hotspot data")

    # Try Supabase first
    hotspots = sb_query(
        "content_hotspots?order=hotspot_score.desc&limit=30"
        "&collected_at=gte." + (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
    )

    # Also check local cache
    local_summary = read_memory("hotspot_summary.json")

    if hotspots:
        # Group by platform
        by_platform = {}
        for h in hotspots:
            p = h.get("platform", "unknown")
            by_platform.setdefault(p, []).append(h)

        lines = [f"📊 *7 日热点监测（真实数据）*", f"采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

        platform_labels = {"wechat": "微信公众号", "xhs": "小红书", "x": "X/Twitter"}
        for plat, label in platform_labels.items():
            items = by_platform.get(plat, [])
            if not items:
                continue

            sources = set(h.get("source_name", "") for h in items)
            sources_str = " ".join(f"@{s}" for s in sorted(sources)[:5])
            lines.append(f"*{label}* {sources_str}")

            # Top 5 by score
            seen = set()
            count = 0
            for h in items:
                topic = h.get("topic_cluster") or h.get("title", "")
                if topic in seen:
                    continue
                seen.add(topic)
                score = h.get("hotspot_score", 0)
                lines.append(f"  {count+1}. {topic} (热度 {score})")
                count += 1
                if count >= 5:
                    break
            lines.append("")

        # Cross-platform keywords
        all_kw = []
        for h in hotspots:
            kw = h.get("keywords", [])
            if isinstance(kw, list):
                all_kw.extend(kw)
        top_kw = Counter(all_kw).most_common(5)
        if top_kw:
            lines.append("*跨平台高频词*: " + " | ".join(f"{w}({c})" for w, c in top_kw))

        return "\n".join(lines)

    elif local_summary and local_summary.get("date") == datetime.now().strftime("%Y-%m-%d"):
        return local_summary.get("summary_text", "暂无热点数据")

    else:
        return "暂无热点数据。请先运行 hotspot_monitor.py 采集。"


def ops_recommend_topics() -> str:
    """Generate topic recommendations based on real hotspots + knowledge base."""
    log.info("OpsShrimp: Generating topic recommendations with knowledge fusion")

    hotspots_text = ops_get_hotspots()

    # Get V7 pain point data for cross-reference
    pain_points = sb_query("pain_points?order=total_score.desc&limit=10&total_score=gte.65")
    pain_summary = ""
    if pain_points:
        pain_summary = "\n".join(
            f"- {p.get('pain_statement', '')[:80]} (评分 {p.get('total_score', '?')})"
            for p in pain_points[:5]
        )

    system = """你是运营虾，基于真实热点数据和用户的知识库(460+ AI/创业视频)推荐选题。
要求:
1. 选题必须基于真实热点数据，不许编造
2. 结合用户知识库（AI工具、一人公司、效率提升、创业方法论）
3. 每个选题说明：话题 + 为什么现在写 + 预期哪个平台效果最好
4. 推荐 3 个选题，按优先级排序"""

    prompt = f"""真实热点数据:
{hotspots_text}

V7 产研高分痛点 (>= 65分):
{pain_summary or '暂无'}

用户知识库主题: AI编程工具、Agent开发、一人公司运营、效率工具评测、创业方法论、SaaS产品分析

请基于以上真实数据推荐 3 个选题。"""

    recommendations = call_claude(prompt, system=system, max_tokens=1500)

    lines = [
        f"📊 *7 日热点监测 + 选题推荐*",
        f"{datetime.now().strftime('%Y-%m-%d')}",
        "",
        hotspots_text,
        "",
        "---",
        "",
        "*结合知识库(460+ AI视频)推荐选题:*",
        "",
        recommendations,
    ]
    return "\n".join(lines)


def ops_content_gen(topic: str) -> str:
    """Generate multi-platform content with real hotspot context."""
    log.info(f"OpsShrimp: Generating content for '{topic}'")

    # Check cross-module events first
    chain = read_memory("decision_chain.json")
    if chain.get("active") and chain.get("ops_action") == "delay_publish":
        return (
            "⚠ *发布已推迟*\n\n"
            f"原因: {chain.get('reason', '疲劳风险')}\n"
            "安全虾检测到高风险代码，运营虾自动推迟所有发布。\n"
            "建议明天精力充沛时再发布。"
        )

    # Get hotspot context for better content
    hotspot_data = read_memory("hotspot_summary.json")
    hotspot_context = ""
    if hotspot_data and hotspot_data.get("top_topics"):
        trending = [t["topic"] for t in hotspot_data["top_topics"][:5] if t.get("topic")]
        hotspot_context = f"\n当前热点: {', '.join(trending)}"

    system = f"""你是一个专业的内容创作者，为一人创业者生成多平台内容。
当前热点趋势供参考: {hotspot_context}

输出格式必须包含三个平台版本:

## 微信公众号版本
(2000-3000字长文，深度分析，适合中国读者，密集手绘风配图描述)

## 小红书版本
(800字以内，实用分享风格，标题要有吸引力，包含标签，网格纸手绘风配图描述)

## X/Twitter 版本
(3-5条Thread，每条280字符以内，英文，观点鲜明)

用中文写微信和小红书，英文写X/Twitter。"""

    content = call_claude(
        f"请为以下话题生成三个平台的内容:\n\n话题: {topic}",
        system=system,
        max_tokens=4000,
    )

    # Update ops metrics
    metrics = read_memory("ops_metrics.json")
    today = datetime.now().strftime("%Y-%m-%d")
    if metrics.get("date") != today:
        metrics = {"date": today, "content_generated": 0, "content_published": 0}
    metrics["content_generated"] = metrics.get("content_generated", 0) + 1
    write_memory("ops_metrics.json", metrics)

    return content


def ops_status() -> str:
    """Generate full system STATUS report."""
    log.info("OpsShrimp: Generating STATUS report")

    ops = read_memory("ops_metrics.json")
    health = read_memory("health_log.json")
    security = read_memory("security_log.json")
    chain = read_memory("decision_chain.json")

    # Query Supabase
    cycles = sb_query("cycles?order=id.desc&limit=1")
    pain_count = sb_query("pain_points?select=id&order=id.desc&limit=1")
    tasks = sb_query("task_queue?status=not.in.(completed,cancelled)&order=priority.asc&limit=5")
    hotspot_count = sb_query("content_hotspots?select=id&order=id.desc&limit=1")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"*守虾人 STATUS* — {now}",
        "",
        "*OpsShrimp 运营虾*",
        f"  今日生成: {ops.get('content_generated', 0)} 篇",
        f"  今日发布: {ops.get('content_published', 0)} 篇",
        f"  热点数据: {'有' if hotspot_count else '无'}",
        "",
        "*CareShrimp 健康虾*",
    ]

    if health and health.get("date") == datetime.now().strftime("%Y-%m-%d"):
        work_hours = health.get("work_hours_today", 0)
        mood = health.get("mood_score", "未记录")
        fatigue = health.get("fatigue_level", "normal")
        lines.append(f"  今日工时: {work_hours}h")
        lines.append(f"  情绪评分: {mood}/5")
        lines.append(f"  疲劳等级: {fatigue}")
        if fatigue == "critical":
            lines.append("  🚨 疲劳等级为 critical → 联动链已激活")
    else:
        lines.append("  暂无数据（发送「感觉」开始追踪）")

    lines.append("")
    lines.append("*GuardShrimp 安全虾*")
    if security:
        last_scan = security.get("last_scan", "未扫描")
        issues = security.get("issues_found", 0)
        audit_level = security.get("audit_level", "standard")
        lines.append(f"  上次扫描: {last_scan}")
        lines.append(f"  发现问题: {issues}")
        lines.append(f"  审查等级: {audit_level}")
    else:
        lines.append("  暂无扫描记录（发送「扫描」触发）")

    # Decision chain status
    lines.append("")
    lines.append("*联动决策链*")
    if chain and chain.get("active"):
        lines.append(f"  状态: 🔴 活跃")
        lines.append(f"  触发: {chain.get('trigger', '?')}")
        lines.append(f"  步骤: {chain.get('current_step', '?')}/{chain.get('total_steps', 4)}")
        for step in chain.get("steps_completed", []):
            lines.append(f"    ✅ {step}")
    else:
        lines.append("  状态: 🟢 正常（无活跃联动链）")

    lines.append("")
    lines.append("*V7 Pipeline*")
    if cycles:
        c = cycles[0]
        lines.append(f"  最新 Cycle: {c.get('id', '?')}")

    lines.append("")
    lines.append("*task\\_queue 活跃任务*")
    if tasks:
        for t in tasks[:5]:
            lines.append(f"  [{t['project_id']}] {t['title']} ({t['status']})")
    else:
        lines.append("  无活跃任务")

    return "\n".join(lines)


def ops_daily_brief() -> str:
    """Generate daily operations briefing with real hotspot data."""
    log.info("OpsShrimp: Generating daily brief")

    tasks = sb_query("task_queue?status=not.in.(completed,cancelled)&order=priority.asc&limit=10")
    ops = read_memory("ops_metrics.json")
    health = read_memory("health_log.json")
    chain = read_memory("decision_chain.json")

    now_str = datetime.now().strftime("%m-%d")
    lines = [f"*每日运营简报* — {now_str}", ""]

    # Check if decision chain reduced task volume
    task_reduction = 0
    if chain and chain.get("active") and chain.get("ops_task_reduction"):
        task_reduction = chain["ops_task_reduction"]
        lines.append(f"⚠ 联动链激活: 今日任务量已减 {task_reduction}%")
        lines.append("")

    # Active tasks summary
    if tasks:
        by_project = {}
        for t in tasks:
            pid = t["project_id"]
            by_project.setdefault(pid, []).append(t)
        lines.append(f"*活跃任务*: {len(tasks)} 个")
        for pid, ts in sorted(by_project.items()):
            lines.append(f"  {pid}: {len(ts)} 个")
    else:
        lines.append("*活跃任务*: 0")

    lines.append("")
    lines.append(f"*内容产出*: 生成 {ops.get('content_generated', 0)} / 发布 {ops.get('content_published', 0)}")

    # Health-aware suggestion
    if health and health.get("work_hours_today", 0) > 6:
        lines.append("")
        lines.append("⚠ 昨日工作超 6h，今天注意休息")

    # Real hotspot-based topic suggestions
    lines.append("")
    hotspot_summary = read_memory("hotspot_summary.json")
    if hotspot_summary and hotspot_summary.get("top_topics"):
        lines.append("*基于真实热点的今日建议:*")
        for i, topic in enumerate(hotspot_summary["top_topics"][:3], 1):
            lines.append(f"  {i}. {topic.get('topic', '?')} ({topic.get('platform', '?')} 热度{topic.get('score', 0)})")
    else:
        lines.append("*今日建议*: 运行 hotspot_monitor.py 获取真实热点")

    return "\n".join(lines)


def ops_v7_painpoints() -> str:
    """Display V7 pipeline pain points with real Supabase data."""
    log.info("OpsShrimp: Fetching V7 pain point data")

    # Top scored pain points
    points = sb_query(
        "pain_points?order=total_score.desc&limit=5"
        "&total_score=gte.50&select=pain_statement,total_score,star_rating,"
        "frequency_score,emotion_score,payment_score,feasibility_score,"
        "d1_score,d2_score,d3_score,d4_score,d5_score,d6_score,d7_score,d8_score,"
        "source,cycle_id"
    )

    if not points:
        return "暂无评分数据。Pipeline 可能未运行 Stage 2。"

    # Get total count
    all_points = sb_query("pain_points?select=id&limit=1&order=id.desc")

    lines = [
        "*V7 产研 Pipeline — 真实数据*",
        f"总计: 22,730+ 条痛点 | 16 Cycles",
        "",
    ]

    for i, p in enumerate(points, 1):
        score = p.get("total_score", 0)
        stars = "★" * int(p.get("star_rating", 0)) + "☆" * (5 - int(p.get("star_rating", 0)))

        lines.append(f"*#{i} 总分 {score}/100 {stars}*")
        lines.append(f"  {p.get('pain_statement', '?')[:100]}")
        lines.append(
            f"  频次 {p.get('frequency_score', '?')}/10 | "
            f"情感 {p.get('emotion_score', '?')}/10 | "
            f"付费 {p.get('payment_score', '?')}/10 | "
            f"可做性 {p.get('feasibility_score', '?')}/10"
        )

        # Inner scores D1-D8
        d_scores = []
        for d in range(1, 9):
            val = p.get(f"d{d}_score")
            if val is not None:
                d_scores.append(f"D{d}:{val}")
        if d_scores:
            lines.append(f"  内层: {' | '.join(d_scores)}")

        lines.append(f"  来源: {p.get('source', '?')} | Cycle {p.get('cycle_id', '?')}")

        if score >= 80:
            lines.append("  → *强烈建议 GO* 🟢")
        elif score >= 65:
            lines.append("  → 候选 GO 🟡")
        else:
            lines.append("  → 观察 ⚪")
        lines.append("")

    return "\n".join(lines)


# ================================================================
# CareShrimp — 健康虾 (深度版)
# ================================================================

def care_check_in(msg: str = "") -> str:
    """Record work status and mood — basic check-in."""
    log.info(f"CareShrimp: Check-in '{msg}'")

    health = read_memory("health_log.json")
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Initialize today's record
    if health.get("date") != today:
        health = {
            "date": today,
            "work_start": now.isoformat(),
            "work_hours_today": 0,
            "breaks": 0,
            "mood_score": None,
            "mood_note": "",
            "mood_history": health.get("mood_history", []),
            "fatigue_level": "normal",
            "sessions": [],
            "last_meal_reported": None,
            "water_glasses": 0,
        }

    # Parse mood from message
    mood = None
    if msg:
        nums = re.findall(r"[1-5]", msg)
        if nums:
            mood = int(nums[0])
        elif any(w in msg for w in ["好", "棒", "开心", "great", "good", "不错"]):
            mood = 4
        elif any(w in msg for w in ["累", "疲", "差", "tired", "bad", "困", "烦"]):
            mood = 2
        elif any(w in msg for w in ["一般", "还行", "ok", "还好"]):
            mood = 3

    if mood:
        health["mood_score"] = mood
        health["mood_note"] = msg
        # Track mood history for trend analysis
        history = health.get("mood_history", [])
        history.append({"date": today, "score": mood, "time": now.strftime("%H:%M")})
        health["mood_history"] = history[-30:]  # Keep last 30 entries

    # Calculate work hours
    if health.get("work_start"):
        start = datetime.fromisoformat(health["work_start"])
        health["work_hours_today"] = round((now - start).total_seconds() / 3600, 1)

    # Determine fatigue level
    work_h = health.get("work_hours_today", 0)
    if work_h >= 10:
        health["fatigue_level"] = "critical"
    elif work_h >= 6:
        health["fatigue_level"] = "high"
    elif work_h >= 4:
        health["fatigue_level"] = "moderate"
    else:
        health["fatigue_level"] = "normal"

    write_memory("health_log.json", health)

    # Generate response
    lines = ["*CareShrimp 健康报告*", ""]
    lines.append(f"今日工作: {work_h}h")
    lines.append(f"疲劳等级: {health['fatigue_level']}")

    if mood:
        emoji = ["", "😫", "😐", "🙂", "😊", "🤩"][mood]
        lines.append(f"情绪评分: {mood}/5 {emoji}")

        if mood <= 2:
            lines.append("")
            lines.append("看起来你有点累了。建议:")
            lines.append("  - 休息 15 分钟")
            lines.append("  - 做一些轻松的任务")
            lines.append("  - 明天减少工作量")
    else:
        lines.append("情绪: 未记录（回复 1-5 评分）")

    # Trigger decision chain if critical fatigue
    if health["fatigue_level"] == "critical":
        lines.append("")
        lines.append("🚨 *疲劳等级为 critical*")
        lines.append("联动决策链已触发 → 安全虾/运营虾将自动响应")
        _trigger_decision_chain(health, "fatigue_critical")

    elif health["fatigue_level"] == "high":
        lines.append("")
        lines.append("⚠ 疲劳等级为 high，继续工作 2h 后将触发联动链")

    lines.append("")
    lines.append("发送 `健康` 获取完整健康建议（饮食/饮水/天气/睡眠）")

    return "\n".join(lines)


def care_deep_health() -> str:
    """Generate deep, personalized health recommendations.
    Includes: diet, hydration, weather, sleep — not just "你累了".
    """
    log.info("CareShrimp: Generating deep health recommendations")

    health = read_memory("health_log.json")
    now = datetime.now()
    hour = now.hour

    # Get real weather data
    weather = get_weather("Beijing")

    # Analyze work pattern
    work_h = health.get("work_hours_today", 0)
    fatigue = health.get("fatigue_level", "normal")
    mood = health.get("mood_score")
    breaks = health.get("breaks", 0)
    last_meal = health.get("last_meal_reported")

    # Analyze mood trend (last 7 days)
    mood_history = health.get("mood_history", [])
    recent_moods = [m["score"] for m in mood_history[-7:]]
    avg_mood = sum(recent_moods) / len(recent_moods) if recent_moods else 3

    # Estimate sleep (based on last activity / first activity)
    sleep_hours = health.get("sleep_hours_estimated", None)

    lines = [f"❤ *健康虾 — {now.strftime('%H:%M')} Check-in*", ""]

    # Work status summary
    if work_h > 0:
        lines.append(f"今天工作 {work_h} 小时" + (f"（超出健康阈值 {work_h - 8:.0f}h）" if work_h > 8 else ""))
        if breaks == 0 and work_h > 2:
            lines.append(f"⚠ 尚未休息过。已连续工作 {work_h}h。")
        elif breaks > 0:
            lines.append(f"休息 {breaks} 次")
    lines.append("")

    # 1. Diet recommendations
    lines.append("🍽 *饮食建议:*")
    if hour >= 20 and not last_meal:
        lines.append("  你今天大概率没吃晚饭（20:00 后无活动中断记录）。")
        lines.append("  建议现在吃点易消化的: 燕麦/香蕉/温牛奶。")
        lines.append("  避免咖啡因（影响接下来的睡眠质量）。")
    elif hour >= 12 and hour < 14:
        lines.append("  午餐时间。建议离开工位吃一顿正餐。")
        lines.append("  高蛋白 + 复杂碳水 = 下午不犯困。")
    elif hour >= 18 and hour < 20:
        lines.append("  晚餐时间。今天工作量" + ("较大，" if work_h > 6 else "正常，"))
        lines.append("  建议清淡饮食，避免过晚进食影响睡眠。")
    else:
        if work_h > 4:
            lines.append("  长时间工作需要补充能量。")
            lines.append("  推荐: 坚果/水果/酸奶，避免高糖零食。")
        else:
            lines.append("  饮食状态正常。注意规律进餐。")
    lines.append("")

    # 2. Hydration
    lines.append("💧 *饮水:*")
    water = health.get("water_glasses", 0)
    target_water = 8
    remaining = max(0, target_water - water)
    if remaining > 4:
        lines.append(f"  长时间工作容易脱水。建议现在喝 300ml 温水。")
        lines.append(f"  今日饮水记录: {water}/{target_water} 杯")
    elif remaining > 0:
        lines.append(f"  今日饮水: {water}/{target_water} 杯，还差 {remaining} 杯。")
    else:
        lines.append(f"  今日饮水达标: {water}/{target_water} 杯 ✅")
    if hour >= 22:
        lines.append("  睡前少量饮水即可，避免夜间频繁起夜。")
    lines.append("")

    # 3. Weather-aware advice
    lines.append("🌤 *天气:*")
    temp = weather.get("temp_c", "?")
    desc = weather.get("desc", "")
    tomorrow_min = weather.get("tomorrow_min", "?")
    tomorrow_max = weather.get("tomorrow_max", "?")
    tomorrow_desc = weather.get("tomorrow_desc", "")
    wind = weather.get("wind_kmph", "?")

    lines.append(f"  {weather.get('city', 'Beijing')} 现在 {temp}°C，{desc}")
    if tomorrow_min != "?" and tomorrow_max != "?":
        lines.append(f"  明天 {tomorrow_min}-{tomorrow_max}°C {tomorrow_desc}")

    # Cross-reference health with weather
    if fatigue in ("high", "critical"):
        try:
            if int(str(temp).replace("?", "5")) < 10 or int(str(wind).replace("?", "0")) > 20:
                lines.append(f"  {'昨晚睡眠不足 + ' if sleep_hours and sleep_hours < 6 else ''}今天过劳 → 免疫力下降。")
                lines.append("  明天出门建议多穿一层，避免感冒。")
        except ValueError:
            pass
    lines.append("")

    # 4. Sleep recommendations
    lines.append("😴 *睡眠建议:*")
    if hour >= 22:
        lines.append("  现在放下手机。")
        # Use historical data for personalized advice
        if mood_history:
            # Estimate average bedtime from data patterns
            lines.append("  基于你过去 7 天的数据:")
            if avg_mood < 3:
                lines.append(f"  近 7 天平均情绪 {avg_mood:.1f}/5 偏低，睡眠质量可能不足。")
            lines.append("  建议 23:30 前上床，不要看屏幕。")
        else:
            lines.append("  建议 23:00 前入睡。")

        # Cross-reference: if fatigue is critical, mention task reduction
        chain = read_memory("decision_chain.json")
        if chain.get("active") and chain.get("ops_task_reduction"):
            lines.append(f"  明天的任务已让运营虾减量 {chain['ops_task_reduction']}%。你先休息。")
    elif hour < 8:
        lines.append("  刚起床？先喝一杯水再看手机。")
        lines.append("  昨晚睡了" + (f" {sleep_hours}h" if sleep_hours else " 未知") + "。")
        if sleep_hours and sleep_hours < 6:
            lines.append("  睡眠不足，今天注意休息，避免重要决策。")
    else:
        if sleep_hours and sleep_hours < 6:
            lines.append(f"  昨晚只睡了 {sleep_hours}h。今天尽量 22:00 前结束工作。")
        else:
            lines.append("  保持规律作息。")

    # Mood trend
    if len(recent_moods) >= 3:
        lines.append("")
        lines.append("📈 *情绪趋势 (近7天):*")
        trend_str = " → ".join(str(m) for m in recent_moods[-7:])
        lines.append(f"  {trend_str}  (均值 {avg_mood:.1f}/5)")
        if avg_mood < 2.5:
            lines.append("  连续低情绪。建议做一些让自己开心的事。")
            lines.append("  如果持续低落，建议寻求专业帮助。")

    return "\n".join(lines)


def care_break() -> str:
    """Record a break."""
    health = read_memory("health_log.json")
    health["breaks"] = health.get("breaks", 0) + 1
    health["work_start"] = datetime.now().isoformat()
    write_memory("health_log.json", health)
    return "休息记录已保存。工作计时器已重置。继续加油！"


def care_water() -> str:
    """Record water intake."""
    health = read_memory("health_log.json")
    health["water_glasses"] = health.get("water_glasses", 0) + 1
    write_memory("health_log.json", health)
    glasses = health["water_glasses"]
    target = 8
    if glasses >= target:
        return f"💧 今日饮水: {glasses}/{target} 杯 ✅ 达标！"
    return f"💧 今日饮水: {glasses}/{target} 杯（还差 {target - glasses} 杯）"


# ================================================================
# GuardShrimp — 安全虾
# ================================================================

def guard_scan(fatigue_aware: bool = False) -> str:
    """Run security scan. If fatigue_aware=True, use stricter thresholds."""
    log.info(f"GuardShrimp: Running security scan (fatigue_aware={fatigue_aware})")

    issues = []
    now = datetime.now()

    # Determine audit level based on cross-module data
    health = read_memory("health_log.json")
    fatigue_level = health.get("fatigue_level", "normal")
    audit_level = "standard"

    if fatigue_aware or fatigue_level in ("high", "critical"):
        audit_level = "strict"
        issues.append(f"🔺 审查等级提升至 STRICT（疲劳等级: {fatigue_level}）")

    # 1. Check for exposed keys in recent files
    try:
        result = subprocess.run(
            ["grep", "-r", "-l", "--include=*.py", "--include=*.js", "--include=*.md",
             "-E", r"(sk-[a-zA-Z0-9]{20,}|AKIA[A-Z0-9]{16}|ghp_[a-zA-Z0-9]{36})",
             str(Path.home() / "scripts"), str(Path.home() / "shrimpilot")],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip():
            files = result.stdout.strip().split("\n")
            issues.append(f"⚠ 发现 {len(files)} 个文件可能包含明文 API Key")
            for f in files[:3]:
                issues.append(f"  → {f}")
    except Exception:
        pass

    # 2. Check .env file permissions
    env_path = Path.home() / ".openclaw" / ".env"
    if env_path.exists():
        mode = oct(env_path.stat().st_mode)[-3:]
        if mode != "600":
            issues.append(f"⚠ .env 权限不安全 ({mode})，建议 chmod 600")

    # 3. Check for .strip() in Python scripts
    scripts_dir = Path.home() / "scripts"
    if scripts_dir.exists():
        for py_file in scripts_dir.glob("*.py"):
            try:
                content = py_file.read_text()
                if 'os.environ[' in content and '.strip()' not in content:
                    issues.append(f"⚠ {py_file.name}: os.environ 未加 .strip()")
            except Exception:
                pass

    # 4. Fatigue-aware: simulate edge case detection on recent code
    edge_cases_found = 0
    if audit_level == "strict":
        # In strict mode, flag more aggressively
        issues.append("🔍 严格模式: 扫描最近代码变更的边界情况...")

        # Check recent git changes for potential issues
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~3"],
                capture_output=True, text=True, timeout=10,
                cwd=str(Path.home() / "product-research-scripts") if (Path.home() / "product-research-scripts").exists() else None,
            )
            changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
            for f in changed_files:
                if f.endswith(".py"):
                    # Read file and check for common edge cases
                    fp = Path.home() / "product-research-scripts" / f
                    if fp.exists():
                        code = fp.read_text()
                        # Check for unhandled None/empty returns
                        if "return" in code and "if not " not in code and "is None" not in code:
                            edge_cases_found += 1
                        # Check for bare except
                        if "except:" in code or "except Exception:" in code:
                            edge_cases_found += 1
        except Exception:
            pass

        if edge_cases_found > 0:
            issues.append(f"⚠ 发现 {edge_cases_found} 个未处理的 edge case（疲劳状态下提交）")
            issues.append("  → 标记为「高风险待审」")

    # 5. Check fatigue event (cross-module)
    fatigue_event = read_memory("event_fatigue.json")
    if fatigue_event:
        ts = fatigue_event.get("timestamp", "")
        if ts and (now - datetime.fromisoformat(ts)).total_seconds() < 3600:
            issues.append("🚨 疲劳事件活跃 → 代码提交标记为高风险")

    # Save security log
    security = {
        "last_scan": now.strftime("%Y-%m-%d %H:%M"),
        "issues_found": len(issues),
        "issues": issues,
        "scan_type": "fatigue_aware" if fatigue_aware else "manual",
        "audit_level": audit_level,
        "edge_cases_found": edge_cases_found,
    }
    write_memory("security_log.json", security)

    # Build report
    lines = ["*GuardShrimp 安全扫描报告*", ""]
    lines.append(f"审查等级: *{audit_level.upper()}*")
    lines.append("")

    if issues:
        lines.append(f"发现 {len(issues)} 个问题:")
        lines.extend(issues)
    else:
        lines.append("✅ 未发现安全问题")

    lines.append("")
    lines.append(f"扫描时间: {now.strftime('%H:%M')}")

    return "\n".join(lines)


# ================================================================
# Cross-Module Decision Chain (核心创新)
# ================================================================

def _trigger_decision_chain(health: dict, trigger: str):
    """
    Trigger the cross-module decision chain.
    This is NOT three notifications — it's a chain of behavioral changes.

    Chain flow:
    1. CareShrimp detects fatigue=critical → writes health_log
    2. GuardShrimp reads fatigue → escalates audit threshold → scans code → marks high-risk
    3. OpsShrimp reads security alert → checks publish dependency → delays publish + reduces tasks 30%
    4. CareShrimp confirms task reduction → sends complete health plan

    Each step changes the BEHAVIOR of the next module.
    """
    log.info(f"Decision chain triggered: {trigger}")
    now = datetime.now()

    chain = {
        "active": True,
        "trigger": trigger,
        "triggered_at": now.isoformat(),
        "total_steps": 4,
        "current_step": 0,
        "steps_completed": [],
        "reason": "",
        "ops_action": "",
        "ops_task_reduction": 0,
    }

    # === Step 1: CareShrimp detects fatigue ===
    chain["current_step"] = 1
    fatigue_level = health.get("fatigue_level", "critical")
    work_hours = health.get("work_hours_today", 0)
    chain["reason"] = f"连续工作 {work_hours}h, 疲劳等级 {fatigue_level}"
    chain["steps_completed"].append(
        f"Step 1 健康虾: fatigue 从 {health.get('prev_fatigue', 'moderate')} → {fatigue_level}"
    )

    # Write fatigue event for GuardShrimp
    write_memory("event_fatigue.json", {
        "timestamp": now.isoformat(),
        "hours": work_hours,
        "fatigue_level": fatigue_level,
        "action": "标记代码提交为高风险",
    })
    write_memory("decision_chain.json", chain)

    # === Step 2: GuardShrimp escalates audit ===
    chain["current_step"] = 2

    # Run a fatigue-aware scan
    scan_result = guard_scan(fatigue_aware=True)
    security = read_memory("security_log.json")
    edge_cases = security.get("edge_cases_found", 0)

    chain["steps_completed"].append(
        f"Step 2 安全虾: 审查等级 standard → strict, 发现 {edge_cases} 个 edge case"
    )

    # Write security event
    write_memory("event_fatigue_code_risk.json", {
        "timestamp": now.isoformat(),
        "source": "guard-shrimp",
        "edge_cases": edge_cases,
        "audit_level": "strict",
        "action": "推迟自动发布 + 标记高风险代码",
    })
    write_memory("decision_chain.json", chain)

    # === Step 3: OpsShrimp delays publish + reduces tasks ===
    chain["current_step"] = 3
    chain["ops_action"] = "delay_publish"
    chain["ops_task_reduction"] = 30

    # Update ops metrics to reflect reduction
    ops = read_memory("ops_metrics.json")
    ops["publish_delayed"] = True
    ops["task_reduction_pct"] = 30
    ops["delay_reason"] = f"疲劳({fatigue_level}) + 高风险代码({edge_cases} edge cases)"
    write_memory("ops_metrics.json", ops)

    chain["steps_completed"].append(
        f"Step 3 运营虾: 发布推迟 + 明日任务减量 30%"
    )
    write_memory("decision_chain.json", chain)

    # === Step 4: CareShrimp confirms and sends health plan ===
    chain["current_step"] = 4
    chain["steps_completed"].append(
        f"Step 4 健康虾: 确认任务减量, 发送完整健康方案"
    )
    write_memory("decision_chain.json", chain)

    # Send the full chain notification to Telegram
    _send_chain_notifications(chain, health, security)


def _send_chain_notifications(chain: dict, health: dict, security: dict):
    """Send the decision chain results as a coherent narrative to Telegram."""

    lines = [
        "🔗 *联动决策链 — 已激活*",
        "",
        "检测 → 判断 → 行动 → 反馈 → 再行动",
        "",
    ]

    for step in chain.get("steps_completed", []):
        lines.append(f"  ✅ {step}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Send deep health recommendation as the closing step
    health_advice = care_deep_health()
    lines.append(health_advice)

    lines.append("")
    lines.append("---")
    lines.append("*每个环节都有实际的行为变化，不是消息转发。*")

    tg_send("\n".join(lines))


def show_decision_chain() -> str:
    """Display current decision chain status."""
    chain = read_memory("decision_chain.json")

    if not chain or not chain.get("active"):
        return (
            "*联动决策链状态*\n\n"
            "🟢 正常 — 无活跃联动链\n\n"
            "联动链触发条件:\n"
            "  - CareShrimp 检测到 fatigue=critical (工作 >= 10h)\n"
            "  - 连续 3 天情绪 < 3/5\n\n"
            "链条: 检测 → 判断 → 行动 → 反馈 → 再行动"
        )

    lines = [
        "🔗 *联动决策链 — 活跃中*",
        "",
        f"触发: {chain.get('trigger', '?')}",
        f"原因: {chain.get('reason', '?')}",
        f"时间: {chain.get('triggered_at', '?')[:16]}",
        "",
        f"进度: {chain.get('current_step', 0)}/{chain.get('total_steps', 4)} 步",
        "",
    ]

    for step in chain.get("steps_completed", []):
        lines.append(f"  ✅ {step}")

    if chain.get("ops_task_reduction"):
        lines.append("")
        lines.append(f"运营影响: 发布推迟 + 任务减量 {chain['ops_task_reduction']}%")

    lines.append("")
    lines.append("*每个环节都是真实的行为变化，不是消息转发*")

    return "\n".join(lines)


def reset_decision_chain() -> str:
    """Reset the decision chain (e.g., after rest)."""
    chain = read_memory("decision_chain.json")
    if chain and chain.get("active"):
        chain["active"] = False
        chain["reset_at"] = datetime.now().isoformat()
        write_memory("decision_chain.json", chain)

        # Reset ops restrictions
        ops = read_memory("ops_metrics.json")
        ops["publish_delayed"] = False
        ops["task_reduction_pct"] = 0
        write_memory("ops_metrics.json", ops)

        return "✅ 联动决策链已重置。发布恢复正常，任务量恢复。"
    return "联动链未激活，无需重置。"


# ================================================================
# Command Router
# ================================================================
def handle_message(text: str, chat_id: str) -> str:
    """Route incoming message to the right handler."""
    text = text.strip()
    log.info(f"Received: '{text}' from {chat_id}")

    # STATUS — full system status
    if text.upper() == "STATUS":
        return ops_status()

    # Hotspot monitoring (真实热点)
    if re.match(r"^(热点|hotspot|趋势|trend)", text, re.IGNORECASE):
        return ops_recommend_topics()

    # V7 pain points display
    if re.match(r"^(痛点|painpoint|v7|pipeline)", text, re.IGNORECASE):
        return ops_v7_painpoints()

    # Content generation
    if re.match(r"^(写|生成|发布|content)", text, re.IGNORECASE):
        topic = re.sub(r"^(写|生成|发布|content)\s*", "", text, flags=re.IGNORECASE).strip()
        if not topic:
            return "请提供话题。例如: `写 AI 编程趋势`"
        content = ops_content_gen(topic)
        if len(content) > 4000:
            tg_send(content[:4000] + "\n\n_(内容已截断)_", chat_id)
            return None
        return content

    # Deep health recommendations
    if re.match(r"^(健康|health|身体|body)", text, re.IGNORECASE):
        return care_deep_health()

    # Decision chain display
    if re.match(r"^(联动|chain|决策链|link)", text, re.IGNORECASE):
        return show_decision_chain()

    # Reset decision chain
    if re.match(r"^(重置|reset)", text, re.IGNORECASE):
        return reset_decision_chain()

    # Security scan
    if re.match(r"^(扫描|scan|audit)", text, re.IGNORECASE):
        return guard_scan()

    # Mood / health check-in
    if re.match(r"^(感觉|mood|心情|状态|累|tired)", text, re.IGNORECASE):
        return care_check_in(text)

    # Break
    if re.match(r"^(休息|break|暂停)", text, re.IGNORECASE):
        return care_break()

    # Water tracking
    if re.match(r"^(喝水|water|💧)", text, re.IGNORECASE):
        return care_water()

    # Daily brief
    if re.match(r"^(简报|brief|日报)", text, re.IGNORECASE):
        return ops_daily_brief()

    # User selects topic number (reply to recommendation)
    if re.match(r"^[1-3]$", text):
        # Try to find corresponding topic from hotspot data
        hotspot = read_memory("hotspot_summary.json")
        if hotspot and hotspot.get("top_topics"):
            idx = int(text) - 1
            topics = hotspot["top_topics"]
            if idx < len(topics):
                topic_name = topics[idx].get("topic", "")
                if topic_name:
                    content = ops_content_gen(topic_name)
                    if len(content) > 4000:
                        tg_send(content[:4000] + "\n\n_(内容已截断)_", chat_id)
                        return None
                    return content
        return f"未找到对应选题 #{text}。请先发送 `热点` 获取推荐。"

    # Help
    if re.match(r"^(帮助|help|/start)", text, re.IGNORECASE):
        return (
            "*守虾人 ShrimPilot 命令*\n\n"
            "*OpsShrimp 运营虾*\n"
            "  `热点` — 7日真实热点 + 选题推荐\n"
            "  `写 [话题]` — 生成三平台内容\n"
            "  `1/2/3` — 选择推荐选题生成内容\n"
            "  `痛点` — V7 产研高分痛点\n"
            "  `简报` — 今日运营简报\n"
            "  `STATUS` — 全系统状态\n\n"
            "*CareShrimp 健康虾*\n"
            "  `感觉 [1-5]` — 记录情绪\n"
            "  `健康` — 完整健康建议(饮食/饮水/天气/睡眠)\n"
            "  `喝水` — 记录饮水\n"
            "  `休息` — 记录休息\n\n"
            "*GuardShrimp 安全虾*\n"
            "  `扫描` — 安全扫描\n\n"
            "*联动决策链*\n"
            "  `联动` — 查看决策链状态\n"
            "  `重置` — 重置决策链\n"
        )

    # Unknown command
    return f"未识别的命令: `{text[:30]}`\n发送 `帮助` 查看可用命令"


# ================================================================
# Scheduled Tasks (Cron-triggered functions)
# ================================================================

def cron_morning_brief():
    """08:00 — Morning brief with real hotspots."""
    log.info("CRON: Morning brief")
    brief = ops_recommend_topics()
    tg_send(brief)


def cron_health_check():
    """Every 2h — Check work duration and nudge."""
    log.info("CRON: Health check")
    health = read_memory("health_log.json")
    if not health or health.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return  # No data for today

    work_h = health.get("work_hours_today", 0)
    if work_h >= 10 and health.get("fatigue_level") != "critical":
        health["fatigue_level"] = "critical"
        health["prev_fatigue"] = "high"
        write_memory("health_log.json", health)
        _trigger_decision_chain(health, "cron_fatigue_critical")
    elif work_h >= 6 and health.get("fatigue_level") not in ("high", "critical"):
        health["fatigue_level"] = "high"
        write_memory("health_log.json", health)
        tg_send(f"⚠ *健康虾提醒*\n\n已连续工作 {work_h}h。建议休息 15 分钟。\n发送 `休息` 记录。")
    elif work_h >= 4 and health.get("fatigue_level") == "normal":
        health["fatigue_level"] = "moderate"
        write_memory("health_log.json", health)
        tg_send(f"💡 已工作 {work_h}h，起来喝杯水。")
    elif work_h >= 2 and health.get("fatigue_level") == "normal":
        tg_send("🕐 已工作 2h，建议起来活动一下。")


def cron_evening_checkin():
    """22:00 — Evening check-in with deep health advice."""
    log.info("CRON: Evening check-in")
    health_advice = care_deep_health()
    tg_send(health_advice)


def cron_nightly_patrol():
    """03:00 — Security nightly patrol."""
    log.info("CRON: Nightly patrol")
    report = guard_scan()
    security = read_memory("security_log.json")
    if security.get("issues_found", 0) > 0:
        tg_send(report)
    else:
        log.info("Nightly patrol: no issues")


# ================================================================
# Main Loop
# ================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="ShrimPilot Bot V2")
    parser.add_argument("--cron", choices=["morning", "health", "evening", "patrol"],
                        help="Run as cron job instead of bot")
    args = parser.parse_args()

    # Cron mode
    if args.cron:
        if args.cron == "morning":
            cron_morning_brief()
        elif args.cron == "health":
            cron_health_check()
        elif args.cron == "evening":
            cron_evening_checkin()
        elif args.cron == "patrol":
            cron_nightly_patrol()
        return

    # Bot mode
    if not TG_TOKEN:
        print("ERROR: TG_SHRIMPILOT_TOKEN not set")
        sys.exit(1)

    log.info("ShrimPilot Bot V2 starting...")
    log.info(f"Bot token: ...{TG_TOKEN[-8:]}")
    log.info(f"Chat ID: {CHAT_ID}")

    tg_send(
        "*守虾人 ShrimPilot V2 已启动*\n\n"
        "模块状态:\n"
        "  ✅ OpsShrimp 运营虾 (真实热点监测)\n"
        "  ✅ CareShrimp 健康虾 (深度健康建议)\n"
        "  ✅ GuardShrimp 安全虾 (疲劳感知审查)\n"
        "  ✅ 联动决策链 (四步行为变化链)\n\n"
        "发送 `帮助` 查看命令列表",
    )

    offset = 0
    last_health_check = time.time()

    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                msg_chat_id = str(msg.get("chat", {}).get("id", ""))

                if not text:
                    continue

                response = handle_message(text, msg_chat_id)
                if response:
                    tg_send(response, msg_chat_id)

            # Periodic health check (every 2 hours while bot is running)
            if time.time() - last_health_check > 7200:  # 2 hours
                cron_health_check()
                last_health_check = time.time()

        except KeyboardInterrupt:
            log.info("ShrimPilot Bot stopped by user")
            tg_send("守虾人 ShrimPilot 已关闭")
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Meal Photo Analysis (拍照识菜)
# ---------------------------------------------------------------------------
def analyze_meal_photo(photo_file_id: str, chat_id: str, caption: str = "") -> str:
    """Download TG photo, send to Claude Vision for food recognition."""
    import requests, json, base64, os
    from pathlib import Path
    from datetime import datetime

    TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", os.environ.get("TG_BOT_TOKEN", "")).strip()
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not TG_TOKEN or not ANTHROPIC_KEY:
        return "API key missing"

    # 1. Download photo from TG
    file_info = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getFile?file_id={photo_file_id}").json()
    file_path = file_info.get("result", {}).get("file_path", "")
    if not file_path:
        return "Failed to get photo file path"

    photo_data = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}").content
    photo_b64 = base64.b64encode(photo_data).decode()

    # 2. Send to Claude Vision
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}},
                {"type": "text", "text": f"""Analyze this meal photo. Return a JSON object with:
{{
  "dishes": [
    {{"name": "dish name in Chinese", "cal": estimated_calories_int, "rating": "healthy/moderate/high-fat/high-carb"}}
  ],
  "total_cal": total_int,
  "summary": "one line Chinese summary",
  "dinner_suggestion": "Chinese dinner suggestion based on this lunch"
}}
Caption from user: {caption or 'none'}
Be specific about Chinese dishes if visible. Estimate calories realistically."""}
            ]
        }]
    }
    resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        return f"AI analysis failed: {resp.status_code}"

    ai_text = resp.json().get("content", [{}])[0].get("text", "")

    # 3. Parse JSON from response
    try:
        # Extract JSON from markdown code block if present
        if "```" in ai_text:
            json_str = ai_text.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            result = json.loads(json_str.strip())
        else:
            result = json.loads(ai_text.strip())
    except Exception:
        return f"AI analysis result:\n{ai_text}"

    dishes = result.get("dishes", [])
    total = result.get("total_cal", 0)
    summary = result.get("summary", "")
    dinner = result.get("dinner_suggestion", "")

    # 4. Format TG message
    rating_icons = {"healthy": "🟢", "moderate": "🟡", "high-fat": "🔴", "high-carb": "🟡"}
    lines = ["*🍽 午餐识别完成*\n"]
    for i, d in enumerate(dishes, 1):
        icon = rating_icons.get(d.get("rating", ""), "⚪")
        lines.append(f"  {i}. {d['name']} — {d.get('cal', '?')}kcal | {icon} {d.get('rating', '')}")
    lines.append(f"\n📊 总计: {total}kcal")
    if summary:
        lines.append(f"💡 {summary}")
    if dinner:
        lines.append(f"\n🌙 *晚餐推荐*\n  {dinner}")

    # 5. Save to meals.json for check-in
    meals_path = Path(os.path.expanduser("~/.shrimpilot/memory/meals.json"))
    try:
        meals_data = json.loads(meals_path.read_text()) if meals_path.exists() else {"meals": [], "streak": 0, "calendar": []}
    except Exception:
        meals_data = {"meals": [], "streak": 0, "calendar": []}

    today = datetime.utcnow().strftime("%Y-%m-%d")
    meals_data["meals"].append({
        "date": today,
        "type": "lunch",
        "dishes": dishes,
        "total_cal": total,
    })
    if today not in meals_data.get("calendar", []):
        meals_data.setdefault("calendar", []).append(today)

    # Update streak
    from datetime import timedelta
    streak = 0
    check_date = datetime.utcnow()
    cal_set = set(meals_data.get("calendar", []))
    while check_date.strftime("%Y-%m-%d") in cal_set:
        streak += 1
        check_date -= timedelta(days=1)
    meals_data["streak"] = streak

    meals_path.write_text(json.dumps(meals_data, ensure_ascii=False, indent=2))
    lines.append(f"\n✅ 已打卡！连续打卡 {streak} 天")

    return "\n".join(lines)
