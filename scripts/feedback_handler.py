import sys
if "/home/ec2-user/scripts" not in sys.path:
    sys.path.insert(0, "/home/ec2-user/scripts")
from llm_client import call_llm_vision
#!/usr/bin/env python3
"""
Feedback Handler — TG 截图反馈 → Vision 识别 → 定向修改 → 重新部署

用户通过 TG 发送截图 + 描述 → Claude Vision 识别问题 → 生成修复建议 → 可选自动修复

Usage:
  python3 feedback_handler.py --poll          # 持续轮询 TG 消息
  python3 feedback_handler.py --once          # 处理一次最新消息
  python3 feedback_handler.py --image /path/to/screenshot.png --desc "按钮太小"

Env: ANTHROPIC_API_KEY, TG_SHRIMPILOT_TOKEN, TG_SHRIMPILOT_CHAT_ID
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()

FEEDBACK_DIR = os.path.expanduser("~/.shrimpilot/feedback/")
os.makedirs(FEEDBACK_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("feedback-handler")


# ── TG Helpers ──────────────────────────────────────────────────────────────

def tg_get_updates(offset: int = 0) -> list[dict]:
    """Get new TG messages."""
    if not TG_TOKEN:
        return []
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
    try:
        resp = httpx.get(url, params=params, timeout=35)
        data = resp.json()
        return data.get("result", [])
    except Exception as e:
        log.warning("TG poll error: %s", e)
        return []


def tg_download_photo(file_id: str) -> bytes | None:
    """Download a photo from TG."""
    if not TG_TOKEN:
        return None
    try:
        # Get file path
        resp = httpx.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        file_path = resp.json().get("result", {}).get("file_path")
        if not file_path:
            return None

        # Download file
        resp = httpx.get(
            f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}",
            timeout=30,
        )
        return resp.content
    except Exception as e:
        log.warning("Photo download error: %s", e)
        return None


def tg_send(text: str) -> bool:
    """Send message to TG."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info("TG send (no creds): %s", text[:200])
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        log.warning("TG send error: %s", e)
        return False


# ── Vision Analysis ─────────────────────────────────────────────────────────

def analyze_screenshot(image_data: str, user_description: str = "") -> dict:
    """Analyze screenshot via Gemini Vision."""
    from llm_client import call_llm_vision
    import json
    prompt = f"""You are a senior UI/UX designer reviewing a product screenshot.
Analyze and output JSON:
{{
  "issues": [{{"severity": "high/medium/low", "area": "...", "description": "...", "fix": "..."}}],
  "suggestions": ["..."],
  "positive": ["..."],
  "overall_quality": "good/fair/poor",
  "priority_fix": "..."
}}
User feedback: {user_description}"""
    result = call_llm_vision("gemini-flash", "", prompt, image_data, max_tokens=4000)
    try:
        return json.loads(result)
    except:
        return {{"raw": result}}


def process_feedback(image_data: bytes, description: str, source: str = "tg") -> dict:
    """Full feedback processing pipeline."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    feedback_id = f"fb_{timestamp}"

    # Save screenshot
    img_path = os.path.join(FEEDBACK_DIR, f"{feedback_id}.png")
    with open(img_path, "wb") as f:
        f.write(image_data)

    # Analyze
    analysis = analyze_screenshot(image_data, description)

    # Save analysis
    result = {
        "feedback_id": feedback_id,
        "timestamp": timestamp,
        "source": source,
        "description": description,
        "image_path": img_path,
        "analysis": analysis,
    }

    json_path = os.path.join(FEEDBACK_DIR, f"{feedback_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Format TG response
    a = analysis
    if "error" not in a:
        issues = a.get("issues", [])
        quality = a.get("overall_quality", "?")
        priority = a.get("priority_fix", "N/A")

        msg = f"🔍 *反馈分析完成* (`{feedback_id}`)\n\n"
        msg += f"📊 整体质量: *{quality}*\n"
        msg += f"🎯 优先修复: {priority}\n\n"

        if issues:
            msg += f"*发现 {len(issues)} 个问题:*\n"
            for issue in issues[:5]:
                sev_icon = {"critical": "🔴", "major": "🟠", "minor": "🟡"}.get(issue.get("severity"), "⚪")
                msg += f"{sev_icon} [{issue.get('area', '?')}] {issue.get('description', '')}\n"

        suggestions = a.get("suggestions", [])
        if suggestions:
            msg += f"\n*建议:*\n"
            for s in suggestions[:3]:
                msg += f"• {s}\n"
    else:
        msg = f"⚠️ 分析失败: {a.get('raw', 'Unknown error')[:200]}"

    if source == "tg":
        tg_send(msg)

    log.info("Feedback %s processed: %s", feedback_id, a.get("overall_quality", "?"))
    return result


# ── TG Polling ──────────────────────────────────────────────────────────────

def poll_tg(once: bool = False) -> None:
    """Poll TG for screenshot feedback messages."""
    log.info("Starting TG poll (once=%s)...", once)
    offset = 0

    while True:
        updates = tg_get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if chat_id != TG_CHAT_ID:
                continue

            # Check for photo
            photos = msg.get("photo", [])
            caption = msg.get("caption", "")
            text = msg.get("text", "")

            # Check if this is a feedback message
            is_feedback = bool(photos) or any(
                kw in (caption + text).lower()
                for kw in ["反馈", "feedback", "bug", "问题", "修改", "fix", "改"]
            )

            if photos and is_feedback:
                # Get largest photo
                largest = max(photos, key=lambda p: p.get("file_size", 0))
                image_data = tg_download_photo(largest["file_id"])
                if image_data:
                    process_feedback(image_data, caption or text)
            elif text and is_feedback and not photos:
                tg_send("📸 请发送截图 + 描述，我会分析问题并给出修复建议")

        if once:
            break
        time.sleep(5)


# ── CLI Mode ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Feedback Handler")
    parser.add_argument("--poll", action="store_true", help="Continuous TG polling")
    parser.add_argument("--once", action="store_true", help="Process once and exit")
    parser.add_argument("--image", type=str, help="Local image path to analyze")
    parser.add_argument("--desc", type=str, default="", help="Feedback description")
    args = parser.parse_args()

    if args.image:
        img_path = Path(args.image)
        if not img_path.exists():
            log.error("Image not found: %s", args.image)
            sys.exit(1)
        image_data = img_path.read_bytes()
        result = process_feedback(image_data, args.desc, source="cli")
        print(json.dumps(result.get("analysis", {}), indent=2, ensure_ascii=False))
    elif args.poll:
        poll_tg(once=False)
    elif args.once:
        poll_tg(once=True)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
