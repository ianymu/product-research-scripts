#!/usr/bin/env python3
"""
content_pipeline/ai_expand_generator.py — 低匹配度 → AI 扩写生成三平台内容
当 YouTube 匹配度 < 85% 时，结合热点 + Perplexity 深度搜索 + AI 生成

流程:
  1. Perplexity 深度搜索热点话题相关素材
  2. 如有 YouTube 参考素材 → 结合使用
  3. Claude 生成三平台内容 (WeChat长文 + XHS笔记 + X thread)
  4. 所有内容过 content_qa.py 门控
  5. 存入 draft_contents

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import (
    perplexity_search, sb_insert, sb_query, ANTHROPIC_KEY, log,
)

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx


def _claude_generate(system: str, user_prompt: str, max_tokens: int = 4000) -> str:
    """Call Claude for content generation."""
    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY not set")
        return ""
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6-20250514",
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            data = resp.json()
            return data.get("content", [{}])[0].get("text", "")
    except Exception as e:
        log.error(f"Claude generation error: {e}")
        return ""


def _research_topic(topic: str, keywords: list[str]) -> dict:
    """Deep research via Perplexity — 2 rounds."""
    log.info(f"  Researching: {topic}")

    # Round 1: overview
    query1 = (
        f"What are the latest developments and expert opinions about '{topic}' "
        f"in AI and technology? Keywords: {', '.join(keywords[:5])}. "
        f"Include specific data points, expert quotes, and real examples. "
        f"Focus on content from the last 7 days."
    )
    r1 = perplexity_search(query1)

    # Round 2: deeper examples + tools
    query2 = (
        f"What are the best tools, products, or real-world applications related to '{topic}'? "
        f"Include: product names, features, pricing, user reviews, GitHub stars if applicable. "
        f"Focus on actionable recommendations for solopreneurs and indie developers."
    )
    r2 = perplexity_search(query2)

    citations = list(set(r1.get("citations", []) + r2.get("citations", [])))

    return {
        "overview": r1.get("answer", ""),
        "tools_examples": r2.get("answer", ""),
        "citations": citations[:10],
    }


def generate_wechat_article(topic: str, research: dict, youtube_ref: dict = None) -> str:
    """Generate WeChat long-form article from research."""
    youtube_context = ""
    if youtube_ref:
        v = sb_query(f"content_items?select=title_cn,summary_cn,content_full_cn&id=eq.{youtube_ref['id']}&limit=1")
        if v:
            youtube_context = f"\n\nYouTube参考素材:\n标题: {v[0].get('title_cn', '')}\n摘要: {v[0].get('summary_cn', '')[:500]}"

    system = (
        "你是一位专业的微信公众号作者，擅长写 AI 和科技领域的深度文章。\n"
        "风格要求:\n"
        "- 3000-5000字，4-8个章节，每章用 ## 01 标题 格式\n"
        "- 每个章节后留 [IMAGE_X] 占位符\n"
        "- 开头用一句话 hook 吸引读者\n"
        "- 引用真实数据和专家观点 (附来源)\n"
        "- 结尾有行动建议和互动问题\n"
        "- 语气专业但不枯燥，像一个懂行的朋友在分享\n"
        "- 绝不编造数据或引用"
    )

    prompt = (
        f"话题: {topic}\n\n"
        f"调研素材:\n{research['overview'][:3000]}\n\n"
        f"工具和案例:\n{research['tools_examples'][:2000]}\n\n"
        f"引用来源:\n{chr(10).join(research['citations'][:5])}"
        f"{youtube_context}"
    )

    return _claude_generate(system, prompt, max_tokens=6000)


def generate_xhs_note(topic: str, research: dict) -> str:
    """Generate XHS short note from research."""
    system = (
        "你是一位小红书爆款笔记作者，擅长 AI 工具和效率类内容。\n"
        "风格要求:\n"
        "- 600-950字，6-8段\n"
        "- 每段开头用 emoji\n"
        "- 口语化、有感染力，像朋友推荐\n"
        "- 包含具体工具名、使用步骤、效果对比\n"
        "- 结尾用提问引发互动\n"
        "- 标题用「关键词+数字+情绪」格式"
    )

    prompt = (
        f"话题: {topic}\n\n"
        f"素材:\n{research['overview'][:2000]}\n\n"
        f"工具推荐:\n{research['tools_examples'][:1000]}"
    )

    return _claude_generate(system, prompt, max_tokens=1500)


def generate_x_thread(topic: str, research: dict) -> str:
    """Generate X/Twitter thread from research."""
    system = (
        "You write engaging X/Twitter threads about AI and tech.\n"
        "Rules:\n"
        "- 3-5 tweets, each under 280 chars\n"
        "- First tweet: strong hook with a surprising stat or claim\n"
        "- Each tweet adds a new insight\n"
        "- Last tweet: call-to-action + 2-3 hashtags\n"
        "- Separate tweets with '---'\n"
        "- Sound like a real tech founder, NOT like AI"
    )

    prompt = (
        f"Topic: {topic}\n"
        f"Research: {research['overview'][:1500]}\n"
        f"Tools: {research['tools_examples'][:800]}"
    )

    return _claude_generate(system, prompt, max_tokens=1000)


def expand_from_hotspot(match: dict) -> dict:
    """
    Main function: AI expand content from hotspot match.

    Input: match dict from youtube_matcher
    Output: {wechat: str, xhs: str, x_thread: str, saved: bool}
    """
    topic = match.get("hotspot_topic", "unknown")
    keywords = match.get("keywords", [])
    youtube_video = match.get("youtube_video")

    log.info(f"AI expanding: {topic} (match: {match.get('match_score', 0):.0%})")

    # Step 1: Deep research
    research = _research_topic(topic, keywords)

    if not research["overview"]:
        log.warning(f"  No research data for {topic}")
        return {"wechat": "", "xhs": "", "x_thread": "", "saved": False}

    # Step 2: Generate three platforms
    wechat = generate_wechat_article(topic, research, youtube_video)
    xhs = generate_xhs_note(topic, research)
    x_thread = generate_x_thread(topic, research)

    # Step 3: Save drafts to Supabase
    drafts = []
    if wechat:
        drafts.append({
            "platform": "wechat",
            "title": f"{topic} — AI 深度解读",
            "content": wechat,
            "source_type": "ai_expand",
            "youtube_video_id": youtube_video.get("id") if youtube_video else None,
            "hotspot_topic": topic,
            "match_score": match.get("match_score", 0),
            "status": "draft",
        })
    if xhs:
        drafts.append({
            "platform": "xiaohongshu",
            "title": topic,
            "content": xhs,
            "source_type": "ai_expand",
            "hotspot_topic": topic,
            "match_score": match.get("match_score", 0),
            "status": "draft",
        })
    if x_thread:
        drafts.append({
            "platform": "x",
            "title": topic,
            "content": x_thread,
            "source_type": "ai_expand",
            "hotspot_topic": topic,
            "match_score": match.get("match_score", 0),
            "status": "draft",
        })

    saved = sb_insert("draft_contents", drafts) if drafts else False

    log.info(f"  Generated: WeChat={'OK' if wechat else 'FAIL'}, "
             f"XHS={'OK' if xhs else 'FAIL'}, X={'OK' if x_thread else 'FAIL'}")

    return {
        "wechat": wechat,
        "xhs": xhs,
        "x_thread": x_thread,
        "saved": saved,
        "citations": research.get("citations", []),
    }
