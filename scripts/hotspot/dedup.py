#!/usr/bin/env python3
"""
hotspot/dedup.py — 增量采集去重
用 content_hash (SHA256) 实现 Supabase upsert 去重
"""
import json
from hotspot.config import sb_upsert, log


def format_for_supabase(items: list[dict]) -> list[dict]:
    """Convert extracted items to Supabase row format with content_hash."""
    rows = []
    for item in items:
        row = {
            "platform": item.get("platform", "unknown"),
            "source_name": item.get("source_name", "unknown"),
            "source_url": item.get("source_url", ""),
            "title": (item.get("title") or "untitled")[:500],
            "content_preview": (item.get("content_preview") or "")[:500],
            "metrics": json.dumps({
                "posting_pattern": {
                    "estimated_post_time": item.get("estimated_post_time", "未知"),
                },
                **(item.get("metrics") or {}),
            }),
            "keywords": item.get("keywords", []),
            "topic_cluster": item.get("topic_cluster", ""),
            "hotspot_score": min(100, max(0, item.get("hotspot_score", 50))),
            "window_start": item.get("window_start"),
            "window_end": item.get("window_end"),
            "content_hash": item.get("content_hash", ""),
        }
        rows.append(row)
    return rows


def upsert_hotspots(items: list[dict]) -> int:
    """
    Upsert hotspot items to Supabase with content_hash dedup.
    Returns number of items processed.
    """
    rows = format_for_supabase(items)
    if not rows:
        return 0

    # Filter out rows without content_hash
    valid_rows = [r for r in rows if r.get("content_hash")]
    if not valid_rows:
        log.warning("No rows with content_hash to upsert")
        return 0

    success = sb_upsert("content_hotspots", valid_rows, on_conflict="content_hash")
    if success:
        log.info(f"Upserted {len(valid_rows)} hotspot items (deduped by content_hash)")
    return len(valid_rows) if success else 0
