#!/usr/bin/env python3
"""
Unified TG Router — V7 Pipeline + ShrimPilot 统一路由

一个 Bot 接收所有 TG 消息，按关键词/指令分发到 9 个 Agent：
  V7: Orchestrator, DataCollector, PainAnalyzer, MarketValidator, CompetitorAnalyzer, BusinessDesigner
  ShrimPilot: OpsShrimp, CareShrimp, GuardShrimp

路由规则：
  V7 指令 (GO/LOCK/KILL/STATUS...) → Orchestrator
  写/生成/热点/痛点/简报          → OpsShrimp
  感觉/健康/休息/喝水/静音        → CareShrimp
  扫描/scan/audit                → GuardShrimp
  反馈 + 截图                    → FeedbackHandler
  其他                           → Help / Unknown

Usage:
  python3 unified_tg_router.py            # 持续轮询
  python3 unified_tg_router.py --once     # 处理一次

Env: TG_SHRIMPILOT_TOKEN, TG_SHRIMPILOT_CHAT_ID, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("unified-router")

# ── TG Helpers ──────────────────────────────────────────────────────────────

def tg_send(text: str, chat_id: str = "") -> bool:
    """Send message to TG."""
    cid = chat_id or CHAT_ID
    if not TG_TOKEN or not cid:
        log.info("[DRY] TG send: %s", text[:200])
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # Split long messages
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            resp = httpx.post(url, json={
                "chat_id": cid,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=15)
            if resp.status_code != 200:
                # Retry without Markdown if parse fails
                httpx.post(url, json={"chat_id": cid, "text": chunk}, timeout=15)
        except Exception as e:
            log.warning("TG send error: %s", e)
            return False
    return True


def tg_get_updates(offset: int = 0) -> list[dict]:
    """Poll TG for new messages."""
    if not TG_TOKEN:
        return []
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    try:
        resp = httpx.get(url, params={
            "offset": offset,
            "timeout": 30,
            "allowed_updates": ["message"],
        }, timeout=35)
        return resp.json().get("result", [])
    except Exception as e:
        log.warning("TG poll error: %s", e)
        return []


# ── Route Definitions ──────────────────────────────────────────────────────

# V7 Pipeline commands (exact match, case-insensitive)
V7_COMMANDS = {
    "GO": "v7_orchestrator",
    "LOCK": "v7_orchestrator",
    "MAYBE": "v7_orchestrator",
    "KILL": "v7_orchestrator",
    "BUILD": "v7_orchestrator",
    "NEXT": "v7_orchestrator",
    "PAUSE": "v7_orchestrator",
    "RESUME": "v7_orchestrator",
    "LEARN": "v7_orchestrator",
}

# ShrimPilot pattern-based routes
SHRIMP_ROUTES = [
    # OpsShrimp — 运营虾
    (r"^(热点|hotspot|趋势|trend)", "ops_shrimp", "hotspot"),
    (r"^(写|生成|发布|content)", "ops_shrimp", "content"),
    (r"^(痛点|painpoint|v7|pipeline)", "ops_shrimp", "painpoint"),
    (r"^(简报|brief|日报)", "ops_shrimp", "brief"),
    (r"^[1-3]$", "ops_shrimp", "topic_select"),

    # CareShrimp — 健康虾
    (r"^(感觉|mood|心情|状态|累|tired)", "care_shrimp", "mood"),
    (r"^(健康|health|身体|body)", "care_shrimp", "health"),
    (r"^(休息|break|暂停)", "care_shrimp", "break"),
    (r"^(喝水|water|💧)", "care_shrimp", "water"),
    (r"^(静音|mute|安静)\s*(\d+)", "care_shrimp", "mute"),

    # GuardShrimp — 安全虾
    (r"^(扫描|scan|audit)", "guard_shrimp", "scan"),

    # ResearchShrimp — 产研虾
    (r"^(演示|demo)$", "research_shrimp", "demo"),
    (r"^(产研日报|产研简报|research brief)", "research_shrimp", "brief"),
    (r"^(产研建议|产研推荐|research suggest)", "research_shrimp", "suggest"),

    # Decision chain
    (r"^(联动|chain|决策链|link)", "system", "chain"),
    (r"^(重置|reset)", "system", "reset"),

    # Feedback (photo-based, handled separately)
    (r"^(反馈|feedback|bug|修改|fix)", "feedback", "feedback"),
]

# ── Routing Logic ──────────────────────────────────────────────────────────

def route_message(text: str, has_photo: bool = False) -> tuple[str, str, str]:
    """
    Route a message to the right agent.
    Returns: (agent, action, cleaned_text)
    """
    text = text.strip()
    upper = text.upper().split()[0] if text else ""

    # Photo without specific feedback text → meal analysis (CareShrimp)
    # Photo + feedback keywords → feedback handler
    if has_photo:
        if text and any(w in text.lower() for w in ["反馈", "feedback", "bug", "修改", "fix"]):
            return "feedback", "screenshot", text
        return "care_shrimp", "meal_photo", text

    # V7 exact commands
    if upper in V7_COMMANDS:
        args = text[len(upper):].strip()
        return V7_COMMANDS[upper], upper.lower(), args

    # STATUS is special — shows unified status (V7 + ShrimPilot)
    if upper == "STATUS":
        return "unified", "status", ""

    # Pattern matching
    for pattern, agent, action in SHRIMP_ROUTES:
        if re.match(pattern, text, re.IGNORECASE):
            cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            return agent, action, cleaned

    # Help
    if re.match(r"^(帮助|help|/start|/help)", text, re.IGNORECASE):
        return "system", "help", ""

    return "unknown", "unknown", text


def get_help_text() -> str:
    """Unified help message for all 9 agents."""
    return (
        "*🦐 ShrimPilot + V7 统一指令*\n\n"
        "*━━━ V7 产品发现流水线 ━━━*\n"
        "  `GO 1,3` — 批准方向进入验证\n"
        "  `LOCK 1` — 锁定方向进入设计\n"
        "  `MAYBE` — 重跑 LP 实验\n"
        "  `KILL` — 放弃方向\n"
        "  `BUILD` — 启动 MVP 构建\n"
        "  `NEXT` — 下一轮研究循环\n"
        "  `PAUSE` / `RESUME` — 暂停/恢复采集\n"
        "  `LEARN [URL]` — AI 学习\n\n"
        "*━━━ OpsShrimp 运营虾 ━━━*\n"
        "  `热点` — 7 日真实热点 + 选题\n"
        "  `写 [话题]` — 三平台内容生成\n"
        "  `1/2/3` — 选推荐选题\n"
        "  `痛点` — V7 高分痛点\n"
        "  `简报` — 运营日报\n\n"
        "*━━━ CareShrimp 健康虾 ━━━*\n"
        "  `感觉 [1-5]` — 记录情绪\n"
        "  `健康` — 4 维健康建议\n"
        "  `喝水` — 记录饮水\n"
        "  `休息` — 记录休息\n"
        "  `静音 2h` — 静音提醒\n"
        "  发送食物照片 — 自动饮食分析\n\n"
        "*━━━ ResearchShrimp 产研虾 ━━━*\n"
        "  `演示` / `demo` — 9-Agent 全链路演示\n"
        "  `产研日报` — 痛点波动日报\n"
        "  `产研建议` — AI 方向推荐\n\n"
        "*━━━ GuardShrimp 安全虾 ━━━*\n"
        "  `扫描` — 安全审计\n\n"
        "*━━━ 联动 ━━━*\n"
        "  `联动` — 决策链状态\n"
        "  `STATUS` — 全系统仪表盘\n"
        "  `帮助` — 本消息\n\n"
        "📸 发截图 + 描述 → 自动反馈分析"
    )


# ── Main Loop ──────────────────────────────────────────────────────────────

def main():
    """Main TG polling loop with unified routing."""
    import argparse
    parser = argparse.ArgumentParser(description="Unified TG Router")
    parser.add_argument("--once", action="store_true", help="Process once and exit")
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("Unified TG Router — 9 Agent System")
    log.info("=" * 50)

    # Try to import shrimpilot_bot for handler functions
    # This allows the router to delegate to existing handlers
    bot_module = None
    bot_path = Path(__file__).parent.parent.parent / "shrimpilot" / "shrimpilot_bot.py"
    if bot_path.exists():
        sys.path.insert(0, str(bot_path.parent))
        try:
            import shrimpilot_bot
            bot_module = shrimpilot_bot
            log.info("Loaded shrimpilot_bot handlers")
        except ImportError as e:
            log.warning("Could not import shrimpilot_bot: %s", e)

    offset = 0
    while True:
        updates = tg_get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if chat_id != CHAT_ID:
                continue

            text = msg.get("text", "") or msg.get("caption", "")
            has_photo = bool(msg.get("photo"))

            # Handle voice messages via shrimpilot_bot.transcribe_voice
            voice = msg.get("voice")
            if voice and not text and bot_module and hasattr(bot_module, "transcribe_voice"):
                file_id = voice["file_id"]
                tg_send("🎙 正在识别语音...", chat_id)
                transcribed = bot_module.transcribe_voice(file_id)
                tg_send(f"🎙 语音识别：{transcribed}", chat_id)
                text = transcribed  # Process as normal text below

            # Extract photo file_id (largest size = last element)
            photo_file_id = msg["photo"][-1]["file_id"] if msg.get("photo") else None

            agent, action, cleaned = route_message(text, has_photo)
            log.info("[Route] '%s' → agent=%s, action=%s", text[:50], agent, action)

            # Dispatch
            response = None

            if agent == "care_shrimp" and action == "meal_photo" and photo_file_id and bot_module:
                tg_send("正在识别菜品...", chat_id)
                try:
                    response = bot_module.analyze_meal_photo(photo_file_id, chat_id, cleaned)
                except Exception as e:
                    response = f"菜品识别失败: {e}"
                    log.error("Meal photo error: %s", e, exc_info=True)
            elif agent == "system" and action == "help":
                response = get_help_text()
            elif agent == "research_shrimp" and action == "demo":
                # 9-Agent coordination demo
                tg_send("🚀 *启动 9-Agent 全链路协调演示...*", chat_id)
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import demo_coordination
                    importlib.reload(demo_coordination)
                    demo_coordination.run_demo(chat_id)
                    response = None  # demo sends its own messages
                except Exception as e:
                    response = f"⚠️ 演示启动失败: {e}"
                    log.error("Demo error: %s", e, exc_info=True)
            elif agent == "research_shrimp" and action == "brief":
                # Research morning brief
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import research_morning_brief
                    importlib.reload(research_morning_brief)
                    brief_text = research_morning_brief.run_brief()
                    response = brief_text
                except Exception as e:
                    response = f"⚠️ 产研日报生成失败: {e}"
                    log.error("Brief error: %s", e, exc_info=True)
            elif agent == "research_shrimp" and action == "suggest":
                response = "🔬 产研建议功能开发中...\n发送 `产研日报` 查看最新痛点排名"
            elif agent == "unknown":
                response = f"未识别: `{text[:30]}`\n发送 `帮助` 查看命令"
            elif bot_module:
                # Delegate to shrimpilot_bot's handle_message
                try:
                    response = bot_module.handle_message(text, chat_id)
                except Exception as e:
                    response = f"⚠️ 处理错误: {e}"
                    log.error("Handler error: %s", e, exc_info=True)
            else:
                response = f"[{agent}] 收到指令: {action} {cleaned}\n_(Agent 未连接，仅路由记录)_"

            if response:
                tg_send(response, chat_id)

        if args.once:
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
