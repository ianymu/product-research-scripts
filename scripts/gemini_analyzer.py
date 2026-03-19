#!/usr/bin/env python3
"""
gemini_analyzer.py — Gemini 多模态分析 + Embedding
1. 读取 content_hotspots 中有 full_content 的文章
2. Gemini API 分析（主题提取、情感分析、关键洞察）
3. text-embedding-004 生成 embedding 存入 Supabase

铁律 #1: 所有 os.environ 必须 .strip()

运行: python3 gemini_analyzer.py [--limit N] [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

from hotspot.config import SUPABASE_URL, SUPABASE_KEY, log

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def gemini_analyze(content: str, title: str = "") -> dict:
    """
    Use Gemini to analyze article content.
    Returns: {summary, key_insights, sentiment, topics, action_items}
    """
    if not GEMINI_KEY:
        log.error("GEMINI_API_KEY not set")
        return {}

    prompt = f"""分析以下文章内容，返回 JSON 格式:
{{
  "summary": "200字摘要",
  "key_insights": ["洞察1", "洞察2", "洞察3"],
  "sentiment": "positive/neutral/negative",
  "topics": ["主题1", "主题2"],
  "action_items": ["可执行建议1", "可执行建议2"],
  "relevance_to_solopreneur": 0-100
}}

标题: {title}
内容:
{content[:8000]}"""

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{GEMINI_BASE}/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
                },
            )
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

            # Parse JSON from response
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            log.warning("Gemini returned non-JSON response")
            return {"summary": text[:500]}
    except Exception as e:
        log.error(f"Gemini analysis error: {e}")
        return {}


def gemini_embedding(text: str) -> list[float]:
    """
    Generate embedding using text-embedding-004.
    Returns: list of floats (768 dimensions)
    """
    if not GEMINI_KEY:
        log.error("GEMINI_API_KEY not set")
        return []

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{GEMINI_BASE}/models/text-embedding-004:embedContent?key={GEMINI_KEY}",
                json={
                    "model": "models/text-embedding-004",
                    "content": {"parts": [{"text": text[:2048]}]},
                },
            )
            data = resp.json()
            values = data.get("embedding", {}).get("values", [])
            if values:
                log.info(f"Embedding generated: {len(values)} dimensions")
            return values
    except Exception as e:
        log.error(f"Gemini embedding error: {e}")
        return []


def process_articles(limit: int = 10, dry_run: bool = False):
    """
    Process articles from content_hotspots that have full_content but no gemini_analysis.
    """
    log.info(f"Fetching articles to analyze (limit={limit})...")

    # Query articles with full_content but no analysis yet
    sb_headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{SUPABASE_URL}/rest/v1/content_hotspots"
                f"?full_content=not.is.null"
                f"&gemini_analysis=is.null"
                f"&select=id,title,full_content,source_url,platform,source_name"
                f"&order=hotspot_score.desc"
                f"&limit={limit}",
                headers=sb_headers,
            )
            articles = resp.json() if resp.status_code == 200 else []
    except Exception as e:
        log.error(f"Query error: {e}")
        articles = []

    if not articles:
        log.info("No unanalyzed articles found. Try running crawl first.")
        return

    log.info(f"Found {len(articles)} articles to analyze")

    for i, article in enumerate(articles, 1):
        art_id = article.get("id")
        title = article.get("title", "")
        content = article.get("full_content", "")
        log.info(f"  [{i}/{len(articles)}] Analyzing: {title[:50]}...")

        if dry_run:
            log.info("    (dry-run, skipping)")
            continue

        # 1. Gemini analysis
        analysis = gemini_analyze(content, title)
        if not analysis:
            log.warning(f"    Analysis failed for {art_id}")
            continue

        # 2. Generate embedding
        embed_text = f"{title} {analysis.get('summary', '')} {' '.join(analysis.get('topics', []))}"
        embedding = gemini_embedding(embed_text)

        # 3. Update Supabase
        update_data = {
            "gemini_analysis": json.dumps(analysis, ensure_ascii=False),
        }
        if embedding:
            update_data["embedding"] = embedding

        try:
            update_headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }
            with httpx.Client(timeout=15) as client:
                resp = client.patch(
                    f"{SUPABASE_URL}/rest/v1/content_hotspots?id=eq.{art_id}",
                    headers=update_headers,
                    json=update_data,
                )
                if resp.status_code in (200, 204):
                    log.info(f"    Updated: analysis + {'embedding' if embedding else 'no embedding'}")
                else:
                    log.warning(f"    Supabase update failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log.error(f"    Update error: {e}")

    log.info("Gemini analysis complete")


def main():
    parser = argparse.ArgumentParser(description="Gemini Multimodal Analyzer")
    parser.add_argument("--limit", type=int, default=10, help="Max articles to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to Supabase")
    args = parser.parse_args()

    if not GEMINI_KEY:
        print("ERROR: GEMINI_API_KEY not set in environment")
        sys.exit(1)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)

    process_articles(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
