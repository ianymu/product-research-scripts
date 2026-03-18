#!/usr/bin/env python3
"""
content_pipeline/youtube_matcher.py — Supabase YouTube 素材库匹配
查询 content_items (source='youtube') 与今日热点话题匹配度

匹配逻辑:
  1. 热点 keywords → content_items 全文搜索 (title_cn + summary_cn)
  2. 过滤: published_at >= 1个月内 AND 未被已有 draft_contents 使用
  3. 计算匹配度: keyword overlap ratio
  4. 输出: high (>=85%) → Simple 脚本 / low (<85%) → AI 扩写

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import json
import logging
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, ANTHROPIC_KEY, log

# 已发布内容缓存
_published_ids_cache = None


def _get_published_video_ids() -> set:
    """Get set of YouTube video content_ids that already have draft_contents."""
    global _published_ids_cache
    if _published_ids_cache is not None:
        return _published_ids_cache

    # Query draft_contents for youtube_simple sources
    drafts = sb_query(
        "draft_contents?select=youtube_video_id&source_type=eq.youtube_simple&limit=500"
    )
    _published_ids_cache = {d.get("youtube_video_id") for d in drafts if d.get("youtube_video_id")}
    log.info(f"Found {len(_published_ids_cache)} already-published YouTube videos")
    return _published_ids_cache


def _search_youtube_library(keywords: list[str], limit: int = 10) -> list[dict]:
    """
    Search Supabase content_items (source='youtube') by keywords.
    Returns matching videos sorted by relevance.
    """
    one_month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

    # Build OR query for title_cn matching
    # Supabase full-text search via ilike
    results = []
    for kw in keywords[:5]:  # Limit to top 5 keywords to avoid too many queries
        path = (
            f"content_items?select=id,source_id,title,title_cn,summary_cn,author,"
            f"view_count,published_at,source_url,key_points"
            f"&source=eq.youtube"
            f"&published_at=gte.{one_month_ago}"
            f"&or=(title_cn.ilike.*{kw}*,summary_cn.ilike.*{kw}*,title.ilike.*{kw}*)"
            f"&order=view_count.desc.nullslast"
            f"&limit={limit}"
        )
        rows = sb_query(path)
        for row in rows:
            if row not in results:
                results.append(row)

    return results


def _calculate_match_score(hotspot_keywords: list[str], video: dict) -> float:
    """
    Calculate keyword overlap ratio between hotspot and video.
    Returns 0.0 ~ 1.0
    """
    if not hotspot_keywords:
        return 0.0

    video_text = " ".join([
        video.get("title_cn") or "",
        video.get("title") or "",
        video.get("summary_cn") or "",
        " ".join(video.get("key_points") or []),
    ]).lower()

    matched = sum(1 for kw in hotspot_keywords if kw.lower() in video_text)
    return matched / len(hotspot_keywords)


def match_hotspots_to_youtube(hotspot_topics: list[dict]) -> list[dict]:
    """
    Main function: match hotspot topics to YouTube library.

    Input: list of hotspot items with {topic_cluster, keywords, hotspot_score}
    Output: list of matches with {hotspot_topic, match_score, youtube_video, match_type}

    match_type:
      - "high" (>=0.85) → use Simple script
      - "low" (<0.85) → use AI expansion
      - "none" (no match) → pure AI expansion from scratch
    """
    log.info(f"Matching {len(hotspot_topics)} hotspot topics to YouTube library...")

    published_ids = _get_published_video_ids()
    matches = []

    for topic in hotspot_topics:
        keywords = topic.get("keywords", [])
        topic_name = topic.get("topic_cluster") or topic.get("title", "unknown")

        if not keywords:
            matches.append({
                "hotspot_topic": topic_name,
                "hotspot_score": topic.get("hotspot_score", 0),
                "match_score": 0.0,
                "match_type": "none",
                "youtube_video": None,
                "suggestion": "无关键词，需 AI 扩写",
            })
            continue

        # Search YouTube library
        videos = _search_youtube_library(keywords)

        # Filter out already-published
        videos = [v for v in videos if v.get("id") not in published_ids]

        if not videos:
            matches.append({
                "hotspot_topic": topic_name,
                "hotspot_score": topic.get("hotspot_score", 0),
                "match_score": 0.0,
                "match_type": "none",
                "youtube_video": None,
                "suggestion": "YouTube 素材库无匹配，需 AI 扩写",
            })
            continue

        # Calculate match scores and pick best
        best_video = None
        best_score = 0.0
        for video in videos:
            score = _calculate_match_score(keywords, video)
            if score > best_score:
                best_score = score
                best_video = video

        match_type = "high" if best_score >= 0.85 else "low"

        if best_score >= 0.85:
            suggestion = f"匹配度 {best_score:.0%}，可用 Simple 脚本直接生成三平台内容"
        elif best_score >= 0.5:
            suggestion = f"匹配度 {best_score:.0%}，建议结合 YouTube 素材 + AI 扩写"
        else:
            suggestion = f"匹配度 {best_score:.0%}，YouTube 素材参考价值低，建议纯 AI 扩写"

        matches.append({
            "hotspot_topic": topic_name,
            "hotspot_score": topic.get("hotspot_score", 0),
            "match_score": best_score,
            "match_type": match_type,
            "youtube_video": {
                "id": best_video.get("id"),
                "source_id": best_video.get("source_id"),
                "title": best_video.get("title_cn") or best_video.get("title"),
                "author": best_video.get("author"),
                "view_count": best_video.get("view_count"),
                "source_url": best_video.get("source_url"),
            } if best_video else None,
            "suggestion": suggestion,
        })

    # Sort by hotspot_score desc
    matches.sort(key=lambda m: m.get("hotspot_score", 0), reverse=True)

    high_count = sum(1 for m in matches if m["match_type"] == "high")
    low_count = sum(1 for m in matches if m["match_type"] == "low")
    none_count = sum(1 for m in matches if m["match_type"] == "none")
    log.info(f"Match results: {high_count} high, {low_count} low, {none_count} none")

    return matches
