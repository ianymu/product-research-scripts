#!/usr/bin/env python3
"""
ops_tracker/platform_stats.py — 三平台运营数据拉取
微信: 公众号数据统计 API
XHS: Perplexity 搜索估算 (无官方API)
X: v2 API tweet metrics

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, sb_insert, perplexity_search, log

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

WECHAT_APP_ID = os.environ.get("WECHAT_APP_ID", "").strip()
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "").strip()
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "").strip()


def _wechat_access_token() -> str:
    """Get WeChat access token."""
    if not WECHAT_APP_ID or not WECHAT_APP_SECRET:
        return ""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": WECHAT_APP_ID, "secret": WECHAT_APP_SECRET},
            )
            return resp.json().get("access_token", "")
    except Exception as e:
        log.error(f"WeChat token error: {e}")
        return ""


def fetch_wechat_stats(date_str: str = None) -> dict:
    """
    Fetch WeChat article stats for a given date.
    API: POST /datacube/getarticlesummary
    """
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    token = _wechat_access_token()
    if not token:
        log.warning("WeChat: no access_token, returning empty stats")
        return {"platform": "wechat", "stat_date": date_str, "articles_count": 0}

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"https://api.weixin.qq.com/datacube/getarticlesummary?access_token={token}",
                json={"begin_date": date_str, "end_date": date_str},
            )
            data = resp.json()
            items = data.get("list", [])

            total_reads = sum(i.get("int_page_read_count", 0) for i in items)
            total_likes = sum(i.get("like_count", 0) for i in items)
            total_shares = sum(i.get("share_count", 0) for i in items)

            return {
                "platform": "wechat",
                "stat_date": date_str,
                "articles_count": len(items),
                "total_reads": total_reads,
                "total_likes": total_likes,
                "total_shares": total_shares,
                "total_comments": 0,  # Not in this API
                "total_saves": 0,
                "details": {"raw_items": items},
            }
    except Exception as e:
        log.error(f"WeChat stats error: {e}")
        return {"platform": "wechat", "stat_date": date_str, "articles_count": 0}


def fetch_xhs_stats(date_str: str = None) -> dict:
    """
    Estimate XHS stats via Perplexity search (no official API).
    Searches for our account's recent post performance.
    """
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # We can try to search our own account's performance
    # For now, return data from draft_contents metrics
    drafts = sb_query(
        f"draft_contents?select=metrics&platform=eq.xiaohongshu"
        f"&status=eq.published&created_at=gte.{date_str}T00:00:00"
    )

    total_likes = sum(d.get("metrics", {}).get("likes", 0) for d in drafts if isinstance(d.get("metrics"), dict))
    total_saves = sum(d.get("metrics", {}).get("saves", 0) for d in drafts if isinstance(d.get("metrics"), dict))
    total_comments = sum(d.get("metrics", {}).get("comments", 0) for d in drafts if isinstance(d.get("metrics"), dict))

    return {
        "platform": "xhs",
        "stat_date": date_str,
        "articles_count": len(drafts),
        "total_reads": 0,  # XHS doesn't expose reads
        "total_likes": total_likes,
        "total_shares": 0,
        "total_comments": total_comments,
        "total_saves": total_saves,
        "details": {"source": "supabase_draft_metrics", "note": "XHS 无官方API，数据来自手动更新的 draft metrics"},
    }


def fetch_x_stats(date_str: str = None) -> dict:
    """
    Fetch X/Twitter tweet metrics via v2 API.
    GET /2/tweets?ids=...&tweet.fields=public_metrics
    """
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    if not X_BEARER_TOKEN:
        log.warning("X: no bearer token, checking Supabase drafts")
        drafts = sb_query(
            f"draft_contents?select=metrics&platform=eq.x"
            f"&status=eq.published&created_at=gte.{date_str}T00:00:00"
        )
        return {
            "platform": "x",
            "stat_date": date_str,
            "articles_count": len(drafts),
            "total_reads": 0, "total_likes": 0, "total_shares": 0,
            "total_comments": 0, "total_saves": 0,
            "details": {"source": "supabase_draft_metrics"},
        }

    # Fetch published tweet IDs from our drafts
    drafts = sb_query(
        f"draft_contents?select=metrics&platform=eq.x"
        f"&status=eq.published&created_at=gte.{date_str}T00:00:00"
    )
    tweet_ids = []
    for d in drafts:
        m = d.get("metrics", {})
        if isinstance(m, dict) and m.get("tweet_id"):
            tweet_ids.append(m["tweet_id"])

    if not tweet_ids:
        return {"platform": "x", "stat_date": date_str, "articles_count": 0,
                "total_reads": 0, "total_likes": 0, "total_shares": 0,
                "total_comments": 0, "total_saves": 0, "details": {}}

    try:
        ids_str = ",".join(tweet_ids[:100])
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"https://api.twitter.com/2/tweets?ids={ids_str}&tweet.fields=public_metrics",
                headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
            )
            data = resp.json()
            tweets = data.get("data", [])

            total_likes = sum(t.get("public_metrics", {}).get("like_count", 0) for t in tweets)
            total_retweets = sum(t.get("public_metrics", {}).get("retweet_count", 0) for t in tweets)
            total_replies = sum(t.get("public_metrics", {}).get("reply_count", 0) for t in tweets)
            total_impressions = sum(t.get("public_metrics", {}).get("impression_count", 0) for t in tweets)

            return {
                "platform": "x",
                "stat_date": date_str,
                "articles_count": len(tweets),
                "total_reads": total_impressions,
                "total_likes": total_likes,
                "total_shares": total_retweets,
                "total_comments": total_replies,
                "total_saves": 0,  # X has bookmarks but not in basic metrics
                "details": {"tweet_count": len(tweets)},
            }
    except Exception as e:
        log.error(f"X stats error: {e}")
        return {"platform": "x", "stat_date": date_str, "articles_count": 0,
                "total_reads": 0, "total_likes": 0, "total_shares": 0,
                "total_comments": 0, "total_saves": 0, "details": {}}


def fetch_all_stats(date_str: str = None) -> list[dict]:
    """Fetch stats for all three platforms."""
    stats = [
        fetch_wechat_stats(date_str),
        fetch_xhs_stats(date_str),
        fetch_x_stats(date_str),
    ]
    return stats


def save_daily_stats(stats: list[dict]) -> bool:
    """Save daily stats to Supabase ops_daily_stats table."""
    from hotspot.config import sb_upsert
    rows = []
    for s in stats:
        rows.append({
            "platform": s["platform"],
            "stat_date": s["stat_date"],
            "articles_count": s.get("articles_count", 0),
            "total_reads": s.get("total_reads", 0),
            "total_likes": s.get("total_likes", 0),
            "total_shares": s.get("total_shares", 0),
            "total_comments": s.get("total_comments", 0),
            "total_saves": s.get("total_saves", 0),
            "details": s.get("details", {}),
        })
    return sb_upsert("ops_daily_stats", rows, on_conflict="platform,stat_date")
