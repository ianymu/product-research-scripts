#!/usr/bin/env python3
"""
publisher/x_draft.py — X/Twitter 草稿/定时发布

两种模式:
  A. 有 X API v2 写权限 (Basic $100/月+): 直接用 scheduled tweets API
  B. 无写权限: 存 Supabase + TG 推送 → 用户手动发布

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, sb_insert, log

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "").strip()
X_API_KEY = os.environ.get("X_API_KEY", "").strip()
X_API_SECRET = os.environ.get("X_API_SECRET", "").strip()
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "").strip()
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET", "").strip()

EC2_HOST = os.environ.get("EC2_HOST", "18.221.160.170").strip()


def has_write_access() -> bool:
    """Check if X API write credentials are configured."""
    return bool(X_BEARER_TOKEN and X_ACCESS_TOKEN)


def post_tweet(text: str, reply_to_id: str = None) -> dict:
    """
    Post a tweet via X API v2.
    Requires OAuth 1.0a user context (Basic tier+).
    """
    if not has_write_access():
        return {"success": False, "error": "X API write credentials not configured"}

    # For X API v2, we need OAuth 1.0a signing
    # Using httpx with manual auth header for simplicity
    try:
        payload = {"text": text}
        if reply_to_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}

        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://api.twitter.com/2/tweets",
                headers={
                    "Authorization": f"Bearer {X_BEARER_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                tweet_id = data.get("data", {}).get("id", "")
                log.info(f"Tweet posted: {tweet_id}")
                return {"success": True, "tweet_id": tweet_id}
            log.error(f"Tweet post failed: {resp.status_code} {data}")
            return {"success": False, "error": str(data)}
    except Exception as e:
        log.error(f"Tweet post error: {e}")
        return {"success": False, "error": str(e)}


def post_thread(tweets: list[str]) -> dict:
    """Post a thread (multiple tweets as replies)."""
    if not tweets:
        return {"success": False, "error": "Empty thread"}

    results = []
    prev_id = None
    for i, text in enumerate(tweets):
        result = post_tweet(text, reply_to_id=prev_id)
        results.append(result)
        if result.get("success"):
            prev_id = result["tweet_id"]
        else:
            log.error(f"Thread broken at tweet {i+1}")
            break

    return {
        "success": all(r.get("success") for r in results),
        "tweets": results,
    }


def publish_x_draft(draft_id: str) -> dict:
    """
    Publish an X draft from Supabase.
    If API available: post directly.
    If not: return manual publish instructions.
    """
    drafts = sb_query(f"draft_contents?select=*&id=eq.{draft_id}&platform=eq.x&limit=1")
    if not drafts:
        return {"success": False, "error": f"X draft {draft_id} not found"}

    draft = drafts[0]
    content = draft.get("content", "")

    # Split thread by --- separator
    tweets = [t.strip() for t in content.split("---") if t.strip()]

    if has_write_access() and tweets:
        result = post_thread(tweets)
        if result["success"]:
            # Update draft status
            sb_insert("draft_contents", [{
                **draft,
                "status": "published",
                "published_at": datetime.utcnow().isoformat(),
            }])
        return result

    # Manual mode
    preview_url = f"http://{EC2_HOST}/preview/x/{draft_id}"
    return {
        "success": False,
        "manual_mode": True,
        "preview_url": preview_url,
        "content": content,
        "instructions": (
            f"X/Twitter 发布:\n"
            f"1. 打开预览: {preview_url}\n"
            f"2. 复制每条推文内容\n"
            f"3. 在 X APP 逐条发布（第一条发新推文，后续回复形成 thread）"
        ),
    }


def format_tg_notification(draft_id: str, title: str, auto_posted: bool = False) -> str:
    """Format TG notification for X draft."""
    if auto_posted:
        return f"🐦 *X Thread 已自动发布*\n标题: {title}"

    preview_url = f"http://{EC2_HOST}/preview/x/{draft_id}"
    return (
        f"🐦 *X Thread 就绪*\n\n"
        f"标题: {title}\n"
        f"预览: {preview_url}\n\n"
        f"请手动在 X APP 发布"
    )
