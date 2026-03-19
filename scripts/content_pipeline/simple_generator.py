#!/usr/bin/env python3
"""
content_pipeline/simple_generator.py — 高匹配度 → 调用 Simple 脚本生成三平台内容
当 YouTube 匹配度 >= 85% 时，用已有素材直接生成:
  - WeChat: process_article.py (Simple 风格)
  - XHS: process_rednotes.py
  - X: 短推文 thread (Claude 生成)

铁律 #1: 所有 os.environ 必须 .strip()
铁律: 所有内容过 content_qa.py 门控
"""
import os
import sys
import json
import subprocess
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, sb_insert, ANTHROPIC_KEY, log

# Content automation paths
CONTENT_AUTO_BASE = Path(__file__).resolve().parents[3] / "My-One-Person-Company" / "projects" / "content-automation"
WECHAT_SCRIPT = CONTENT_AUTO_BASE / "wechat" / "process_article.py"
REDNOTES_SCRIPT = CONTENT_AUTO_BASE / "rednotes" / "process_rednotes.py"


def generate_x_thread(title: str, summary: str, keywords: list[str] = None) -> str:
    """Generate X/Twitter thread via ChatGPT 5.4 mini."""
    from llm_client import call_llm
    system = "You are a professional X/Twitter thread writer. Write 3-5 tweets, each under 280 characters. Use emojis sparingly. End with a CTA and hashtags. Write in English. Separate tweets with ---"
    user = f"Title: {title}\nSummary: {summary}\nKeywords: {, .join(keywords) if keywords else }"
    return call_llm("chatgpt-5.4-mini", system, user, max_tokens=1500)


def generate_from_youtube(match: dict) -> dict:
    """
    Generate three-platform content from a high-match YouTube video.

    Input: match dict from youtube_matcher with {youtube_video: {id, title, ...}}
    Output: {wechat: bool, xhs: bool, x_thread: str, content_id: str}
    """
    video = match.get("youtube_video")
    if not video or not video.get("id"):
        log.error("No YouTube video in match")
        return {"wechat": False, "xhs": False, "x_thread": "", "content_id": ""}

    content_id = video["id"]
    topic = match.get("hotspot_topic", "")
    log.info(f"Generating 3-platform content for: {video.get('title')} (match: {match.get('match_score', 0):.0%})")

    result = {"content_id": content_id, "wechat": False, "xhs": False, "x_thread": ""}

    # 1. WeChat — call process_article.py
    if WECHAT_SCRIPT.exists():
        try:
            log.info(f"  Running WeChat Simple: {WECHAT_SCRIPT}")
            proc = subprocess.run(
                [sys.executable, str(WECHAT_SCRIPT), content_id],
                capture_output=True, text=True, timeout=300,
                env={**os.environ},
            )
            if proc.returncode == 0:
                result["wechat"] = True
                log.info("  WeChat: OK")
            else:
                log.error(f"  WeChat failed: {proc.stderr[:200]}")
        except Exception as e:
            log.error(f"  WeChat error: {e}")
    else:
        log.warning(f"  WeChat script not found: {WECHAT_SCRIPT}")

    # 2. XHS — call process_rednotes.py
    if REDNOTES_SCRIPT.exists():
        try:
            log.info(f"  Running XHS: {REDNOTES_SCRIPT}")
            proc = subprocess.run(
                [sys.executable, str(REDNOTES_SCRIPT), content_id],
                capture_output=True, text=True, timeout=300,
                env={**os.environ},
            )
            if proc.returncode == 0:
                result["xhs"] = True
                log.info("  XHS: OK")
            else:
                log.error(f"  XHS failed: {proc.stderr[:200]}")
        except Exception as e:
            log.error(f"  XHS error: {e}")
    else:
        log.warning(f"  XHS script not found: {REDNOTES_SCRIPT}")

    # 3. X Thread — generate via Claude
    video_detail = sb_query(
        f"content_items?select=title_cn,summary_cn,key_points&id=eq.{content_id}&limit=1"
    )
    if video_detail:
        v = video_detail[0]
        x_thread = generate_x_thread(
            v.get("title_cn") or video.get("title", ""),
            v.get("summary_cn") or "",
            match.get("keywords", []),
        )
        result["x_thread"] = x_thread

        # Save X draft to Supabase
        sb_insert("draft_contents", [{
            "platform": "x",
            "title": video.get("title", "")[:200],
            "content": x_thread,
            "source_type": "youtube_simple",
            "youtube_video_id": content_id,
            "hotspot_topic": topic,
            "match_score": match.get("match_score", 0),
            "status": "draft",
        }])
        log.info("  X Thread: OK")

    return result
