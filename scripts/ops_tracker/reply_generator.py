#!/usr/bin/env python3
"""
ops_tracker/reply_generator.py — 去AI味负面评论自动回复
流程: 评论输入 → Claude生成回复 → 去AI味后处理 → 存 Supabase → TG推送待审

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import re
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import ANTHROPIC_KEY, sb_insert, log

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# AI典型用语黑名单 (中英文)
AI_PHRASES = [
    "I understand your concern", "Great question", "Indeed", "Absolutely",
    "I appreciate your", "That's a valid point", "I'd be happy to",
    "我理解你的", "非常好的问题", "确实如此", "感谢你的",
    "这是一个很好的观点", "我很高兴", "让我来解释",
    "首先", "其次", "最后",  # overused structure markers
]

# 口语化替换
CASUAL_REPLACEMENTS = {
    "非常感谢": "谢谢",
    "我认为": "我觉得",
    "事实上": "其实",
    "此外": "另外",
    "因此": "所以",
    "然而": "不过",
    "In conclusion": "",
    "To summarize": "",
}


def _deai_filter(text: str) -> str:
    """Remove AI-typical phrases and add casual tone."""
    # Remove AI phrases
    for phrase in AI_PHRASES:
        text = text.replace(phrase, "")

    # Apply casual replacements
    for formal, casual in CASUAL_REPLACEMENTS.items():
        text = text.replace(formal, casual)

    # Remove double spaces and clean up
    text = re.sub(r'\s{2,}', ' ', text).strip()
    text = re.sub(r'^[,，.。、\s]+', '', text)  # Clean leading punctuation

    return text


def generate_reply(
    comment: str,
    platform: str = "wechat",
    context: str = "",
    tone: str = "friendly",
) -> str:
    """
    Generate a de-AI-flavored reply to a comment.

    Args:
        comment: The original comment text
        platform: wechat/xhs/x (affects length limits)
        context: Optional context about the article/post
        tone: friendly/professional/humorous
    """
    if not ANTHROPIC_KEY:
        return f"感谢反馈！{comment[:20]}..."

    max_chars = {"wechat": 200, "xhs": 200, "x": 100}.get(platform, 200)

    system = (
        f"You generate HUMAN-sounding replies to social media comments. "
        f"Platform: {platform}. Max {max_chars} characters.\n\n"
        f"Critical rules:\n"
        f"- Sound like a REAL person typing on their phone\n"
        f"- Use casual language, occasional typo-level informality is OK\n"
        f"- NEVER use phrases like 'Great question!', 'I understand your concern'\n"
        f"- NEVER use bullet points or structured formatting\n"
        f"- Match the emotional tone of the comment\n"
        f"- If negative: acknowledge without being defensive, add value\n"
        f"- If question: give a quick direct answer\n"
        f"- Write in the language of the comment (Chinese or English)\n"
        f"- Keep it SHORT. Real people don't write essays in comments."
    )

    prompt = f"Comment: {comment}"
    if context:
        prompt += f"\nArticle context: {context}"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6-20250514",
                    "max_tokens": 300,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
            reply = data.get("content", [{}])[0].get("text", "")
    except Exception as e:
        log.error(f"Reply generation error: {e}")
        return ""

    # De-AI post-processing
    reply = _deai_filter(reply)

    # Enforce length limit
    if len(reply) > max_chars:
        reply = reply[:max_chars - 3] + "..."

    return reply


def process_comments(comments: list[dict]) -> list[dict]:
    """
    Batch process comments and generate replies.

    Input: list of {platform, comment, author, post_url, context}
    Output: list of {original, reply, platform, status}
    """
    results = []
    for c in comments:
        reply = generate_reply(
            c.get("comment", ""),
            platform=c.get("platform", "wechat"),
            context=c.get("context", ""),
        )

        result = {
            "platform": c.get("platform", ""),
            "original_comment": c.get("comment", ""),
            "original_author": c.get("author", ""),
            "generated_reply": reply,
            "status": "pending",
            "post_url": c.get("post_url", ""),
        }
        results.append(result)

    # Save to Supabase
    if results:
        sb_insert("comment_replies", results)
        log.info(f"Generated {len(results)} replies, saved to comment_replies")

    return results


def format_replies_for_tg(replies: list[dict]) -> str:
    """Format pending replies for TG notification."""
    if not replies:
        return ""

    lines = [f"💬 *{len(replies)} 条评论待回复*", ""]
    for i, r in enumerate(replies[:5]):
        platform = {"wechat": "微信", "xhs": "小红书", "x": "X"}.get(r["platform"], r["platform"])
        lines.append(f"{i+1}. [{platform}] @{r.get('original_author', '匿名')}")
        lines.append(f"   原文: {r['original_comment'][:50]}...")
        lines.append(f"   回复: {r['generated_reply'][:80]}...")
        lines.append("")

    if len(replies) > 5:
        lines.append(f"...还有 {len(replies) - 5} 条")

    return "\n".join(lines)
