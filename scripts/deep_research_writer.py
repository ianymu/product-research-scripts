#!/usr/bin/env python3
"""
Deep Research Writer — MVP 应用 → 公众号 Deep Research 文章

从 pain_aggregator 的应用方向出发，生成深度研究文章：
  产品介绍 → 痛点分析 → 价值主张 → 使用教程(每步截图描述) → 结论

输出：微信公众号长文（Markdown），配 Simple 风格密集手绘配图描述

Usage:
  python3 deep_research_writer.py --direction "AI Coding Assistant" --input pain-aggregation-report.json
  python3 deep_research_writer.py --topic "one-person-company tools" --style deep_research

Env: ANTHROPIC_API_KEY, PERPLEXITY_API_KEY (optional for real-time data)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import httpx
from openai import OpenAI

# ── Config ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("deep-research-writer")

claude = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

# ── Image Description Templates (Simple 密集手绘风) ────────────────────────

IMAGE_STYLE_PROMPT = """
为这篇文章的每个章节生成配图描述（用于 AI 图像生成）。

图片风格要求（Simple 密集手绘风）：
- 白色/米色背景上的密集手绘线条
- 类似在 Moleskine 笔记本上用黑色签字笔画的风格
- 信息密度高：一张图里包含多个相关概念、箭头、标注
- 手写字体标注（英文为主，关键词可中文）
- 适度使用荧光色高亮（黄色/绿色/粉色，最多2色）
- 无照片、无3D、无渐变 — 纯线条+文字
- 尺寸比例：微信公众号 2:1 横图 (900x450px)

每张图的描述应该包含：
1. 主要视觉元素（框图/流程/对比/列表）
2. 关键文字标注
3. 荧光色高亮位置
4. 整体构图（左右分栏/中心辐射/上下流程）
"""


# ── Perplexity Search (optional enhancement) ───────────────────────────────

def perplexity_search(query: str) -> str:
    """Search with Perplexity for real-time data. Returns summary text."""
    if not PERPLEXITY_API_KEY:
        return ""
    try:
        resp = httpx.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": query}],
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content
    except Exception as e:
        log.warning("Perplexity search failed: %s", e)
    return ""


# ── Article Generation ──────────────────────────────────────────────────────

def generate_article(
    direction_name: str,
    direction_data: dict | None = None,
    topic: str = "",
    extra_context: str = "",
) -> dict:
    """Generate a deep research article for WeChat public account."""
    log.info("Generating deep research article: %s", direction_name or topic)

    # Build context
    context_parts = []
    if direction_data:
        context_parts.append(f"应用方向数据:\n{json.dumps(direction_data, ensure_ascii=False, indent=2)}")
    if topic:
        context_parts.append(f"主题: {topic}")

    # Optional: Perplexity enrichment
    search_query = f"latest trends and tools for {direction_name or topic} solopreneur indie hacker 2025 2026"
    perplexity_data = perplexity_search(search_query)
    if perplexity_data:
        context_parts.append(f"实时搜索数据:\n{perplexity_data[:3000]}")
    if extra_context:
        context_parts.append(f"额外上下文:\n{extra_context}")

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""你是一位专注于独立开发者/一人公司领域的深度研究作者，为微信公众号撰写长文。

{f"产品/方向: {direction_name}" if direction_name else f"主题: {topic}"}

{context}

请生成一篇 3000-5000 字的深度研究文章，结构如下：

## 文章结构

### 1. Hook 开场 (200字)
- 用一个真实场景/数据/故事开头，让读者立刻产生共鸣
- 点出核心痛点（不要泛泛而谈）

### 2. 痛点深挖 (500字)
- 3-5 个具体痛点场景（从 V7 痛点数据中提取，如有）
- 每个痛点配一句「用户原话」风格的吐槽
- 数据支撑（市场规模/用户数/增长率）

### 3. 产品/方案介绍 (800字)
- 这个产品/方案是什么（一句话定义）
- 核心功能 3-5 个（每个配使用场景）
- 技术亮点（但不要太技术，让非技术读者也能懂）

### 4. 使用教程 (1000字)
- Step by Step 教程（至少 5 步）
- 每步配截图描述（用 `[图片: 描述]` 标记）
- 每步配一个「小贴士」

### 5. 价值分析 (500字)
- 对比现有方案（表格形式）
- ROI 计算（时间/金钱节省）
- 适合谁用 / 不适合谁用

### 6. 结论 + CTA (200字)
- 总结核心价值
- 行动号召（试用链接/关注引导）

## 写作风格
- 专业但不枯燥，像一个懂行的朋友在跟你聊天
- 多用数据和案例，少用形容词
- 每段不超过 3-4 行（微信阅读体验）
- 适当使用 emoji 但不过度（每段最多 1 个）
- 中英文混排时英文前后加空格

## 配图描述
在文章中每个主要章节后，用以下格式插入配图描述：
```
[配图: 章节名]
风格: Simple 密集手绘风，白色背景，黑色签字笔线条
内容: [具体描述图片内容]
标注: [图片中的文字标注]
高亮: [荧光色标记位置]
构图: [整体布局]
```

输出格式：纯 Markdown，直接可用于公众号排版工具（如 Mdnice）。"""

    resp = claude.chat.completions.create(
        model="gpt-5.4",
        max_completion_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    article_md = resp.choices[0].message.content.strip()

    # Generate image descriptions separately for clarity
    img_prompt = f"""基于以下文章，{IMAGE_STYLE_PROMPT}

文章标题相关: {direction_name or topic}

为以下章节各生成 1 张配图描述（共 6 张）：
1. Hook 开场
2. 痛点深挖
3. 产品介绍
4. 使用教程（总览图）
5. 价值对比
6. 结论 CTA

输出 JSON 数组：
[{{"chapter": "...", "description": "...", "annotations": "...", "highlight_color": "...", "composition": "..."}}]

只输出 JSON。"""

    img_resp = claude.chat.completions.create(
        model="gemini-2.5-flash-image",
        max_completion_tokens=3000,
        messages=[{"role": "user", "content": img_prompt}],
    )
    img_text = img_resp.choices[0].message.content.strip()

    try:
        if "```" in img_text:
            img_text = img_text.split("```")[1].strip()
            if img_text.startswith("json"):
                img_text = img_text[4:].strip()
        image_descriptions = json.loads(img_text)
    except (json.JSONDecodeError, IndexError):
        image_descriptions = []

    return {
        "title": direction_name or topic,
        "article_md": article_md,
        "image_descriptions": image_descriptions,
        "word_count": len(article_md),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Output ──────────────────────────────────────────────────────────────────

def save_article(result: dict, output_dir: str) -> str:
    """Save article and image descriptions."""
    os.makedirs(output_dir, exist_ok=True)

    title_slug = result["title"].lower().replace(" ", "-")[:50]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")

    # Save markdown article
    md_path = os.path.join(output_dir, f"{ts}-{title_slug}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result["article_md"])

    # Save image descriptions
    img_path = os.path.join(output_dir, f"{ts}-{title_slug}-images.json")
    with open(img_path, "w", encoding="utf-8") as f:
        json.dump(result["image_descriptions"], f, indent=2, ensure_ascii=False)

    log.info("Article saved: %s (%d chars)", md_path, result["word_count"])
    return md_path


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep Research Writer")
    parser.add_argument("--direction", type=str, help="Application direction name")
    parser.add_argument("--input", type=str, help="Path to pain-aggregation-report.json")
    parser.add_argument("--topic", type=str, help="Free-form topic (if no direction)")
    parser.add_argument("--output-dir", type=str, default="articles/", help="Output directory")
    parser.add_argument("--index", type=int, default=0, help="Direction index from input JSON (0-based)")
    args = parser.parse_args()

    if not args.direction and not args.topic and not args.input:
        parser.print_help()
        sys.exit(1)

    direction_data = None
    direction_name = args.direction or args.topic or ""

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and args.index < len(data):
            direction_data = data[args.index]
            direction_name = direction_data.get("name", direction_name)

    result = generate_article(direction_name, direction_data, args.topic)
    md_path = save_article(result, args.output_dir)
    print(f"Article: {md_path}")
    print(f"Words: {result['word_count']}")
    print(f"Images: {len(result['image_descriptions'])}")


if __name__ == "__main__":
    main()
