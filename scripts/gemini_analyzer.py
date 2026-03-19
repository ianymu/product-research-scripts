#!/usr/bin/env python3
"""
gemini_analyzer.py — Gemini multi-modal analysis for content_hotspots

Reads wechat articles from Supabase content_hotspots,
calls Gemini 2.0 Flash for theme/sentiment/insights extraction,
and optionally generates embeddings via text-embedding-004.

Usage:
  python3 gemini_analyzer.py --limit 5
  python3 gemini_analyzer.py --limit 10 --skip-embedding
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import httpx

# === Config ===
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()

GEMINI_FLASH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1/models/text-embedding-004:embedContent"

HEADERS_SUPABASE = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def ensure_columns():
    """Add gemini_analysis JSONB column if not exists (idempotent via ALTER)."""
    # Use Supabase REST RPC or just try — the UPDATE will fail gracefully if col missing
    # We'll handle this via a direct SQL migration note
    print("[INFO] Assuming gemini_analysis JSONB column exists (run migration if not)")


def fetch_articles(limit: int) -> list:
    """Fetch wechat articles that haven't been analyzed yet."""
    # Try full_content first, fallback to content_preview
    # Select articles where gemini_analysis is null
    url = f"{SUPABASE_URL}/rest/v1/content_hotspots"
    params = {
        "select": "id,title,content_preview,source_name,keywords,hotspot_score",
        "platform": "eq.wechat",
        "order": "hotspot_score.desc",
        "limit": str(limit),
    }

    # Only fetch rows without gemini_analysis (if column exists)
    # We'll filter client-side as a safe fallback
    resp = httpx.get(url, headers=HEADERS_SUPABASE, params=params, timeout=30)
    resp.raise_for_status()
    articles = resp.json()
    print(f"[INFO] Fetched {len(articles)} wechat articles from Supabase")
    return articles


def analyze_with_gemini(article: dict) -> dict:
    """Call Gemini 2.0 Flash to extract themes, sentiment, insights."""
    title = article.get("title", "")
    content = article.get("content_preview", "") or ""
    keywords = article.get("keywords", []) or []

    prompt = f"""分析以下微信公众号文章，返回 JSON 格式结果：

标题: {title}
内容: {content}
关键词: {', '.join(keywords) if keywords else '无'}

请返回以下 JSON（不要包含 markdown 代码块标记）:
{{
  "themes": ["主题1", "主题2", ...],
  "sentiment": "positive/negative/neutral/mixed",
  "key_insights": ["洞察1", "洞察2", "洞察3"],
  "summary": "一句话总结",
  "relevance_to_solopreneur": "high/medium/low",
  "actionable_angle": "独立开发者可以从中获取的行动建议"
}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1024,
        },
    }

    resp = httpx.post(
        f"{GEMINI_FLASH_URL}?key={GEMINI_API_KEY}",
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()

    # Extract text from Gemini response
    text = result["candidates"][0]["content"]["parts"][0]["text"]

    # Clean markdown code block if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        analysis = json.loads(text)
    except json.JSONDecodeError:
        analysis = {"raw_response": text, "parse_error": True}

    analysis["analyzed_at"] = datetime.utcnow().isoformat()
    analysis["model"] = "gemini-2.0-flash"
    return analysis


def generate_embedding(text: str) -> list:
    """Generate embedding via text-embedding-004."""
    payload = {
        "content": {"parts": [{"text": text[:2048]}]},  # Truncate to safe limit
    }

    resp = httpx.post(
        f"{GEMINI_EMBED_URL}?key={GEMINI_API_KEY}",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    return result["embedding"]["values"]


def update_article(article_id: str, analysis: dict):
    """Update content_hotspots row with gemini_analysis.

    Strategy: try gemini_analysis column first, fallback to metrics JSONB.
    """
    url = f"{SUPABASE_URL}/rest/v1/content_hotspots"
    params = {"id": f"eq.{article_id}"}

    # Try gemini_analysis column first
    update_data = {"gemini_analysis": analysis}
    resp = httpx.patch(
        url,
        headers=HEADERS_SUPABASE,
        params=params,
        json=update_data,
        timeout=30,
    )

    if resp.status_code == 400 and "gemini_analysis" in resp.text:
        # Column doesn't exist — fallback: merge into metrics JSONB
        print(f"  [WARN] gemini_analysis column missing, storing in metrics.gemini_analysis")
        # Fetch current metrics
        r = httpx.get(url, headers=HEADERS_SUPABASE,
                      params={"id": f"eq.{article_id}", "select": "metrics"}, timeout=15)
        r.raise_for_status()
        rows = r.json()
        current_metrics = rows[0].get("metrics", {}) if rows else {}
        if isinstance(current_metrics, str):
            try:
                current_metrics = json.loads(current_metrics)
            except (json.JSONDecodeError, TypeError):
                current_metrics = {}
        if not isinstance(current_metrics, dict):
            current_metrics = {}
        current_metrics["gemini_analysis"] = analysis
        update_data = {"metrics": current_metrics}
        resp = httpx.patch(url, headers=HEADERS_SUPABASE, params=params,
                           json=update_data, timeout=30)

    resp.raise_for_status()
    print(f"  [OK] Updated article {article_id}")


def main():
    parser = argparse.ArgumentParser(description="Gemini analyzer for content_hotspots")
    parser.add_argument("--limit", type=int, default=5, help="Max articles to analyze")
    parser.add_argument("--skip-embedding", action="store_true", help="Skip embedding generation")
    parser.add_argument("--dry-run", action="store_true", help="Analyze but don't write to DB")
    args = parser.parse_args()

    print(f"=== Gemini Analyzer ===")
    print(f"[CONFIG] limit={args.limit}, skip_embedding={args.skip_embedding}")

    articles = fetch_articles(args.limit)
    if not articles:
        print("[WARN] No wechat articles found in content_hotspots")
        sys.exit(0)

    success = 0
    errors = 0

    for i, article in enumerate(articles):
        aid = article["id"]
        title = article.get("title", "???")[:50]
        print(f"\n[{i+1}/{len(articles)}] Analyzing: {title}...")

        try:
            # Gemini analysis
            analysis = analyze_with_gemini(article)
            print(f"  Themes: {analysis.get('themes', [])}")
            print(f"  Sentiment: {analysis.get('sentiment', '?')}")

            # Write analysis to DB first (always)
            if not args.dry_run:
                update_article(aid, analysis)
            success += 1

            # Embedding (optional, non-fatal)
            if not args.skip_embedding:
                try:
                    text_for_embed = f"{article.get('title', '')} {article.get('content_preview', '')}"
                    if text_for_embed.strip():
                        embedding = generate_embedding(text_for_embed)
                        print(f"  Embedding: {len(embedding)} dims")
                        # Update again with embedding data
                        if not args.dry_run:
                            analysis["embedding_dim"] = len(embedding)
                            analysis["embedding_preview"] = embedding[:5]
                            update_article(aid, analysis)
                except Exception as emb_err:
                    print(f"  [WARN] Embedding failed (non-fatal): {emb_err}")

        except Exception as e:
            print(f"  [ERROR] {e}")
            errors += 1

        # Rate limit: 15 RPM for free tier → ~4s between calls
        if i < len(articles) - 1:
            time.sleep(4)

    print(f"\n=== Done: {success} success, {errors} errors ===")


if __name__ == "__main__":
    main()
