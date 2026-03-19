#!/usr/bin/env python3
"""
小红书热点采集器 — 10 个对标账号 (账号制) + 跨账号聚合查询
采集方式: Perplexity 搜索 → Tavily fallback → Claude 提取结构化数据

V2 改动:
  - 增加跨账号聚合查询 (aggregate_queries)
  - Tavily Search 作为 Perplexity 返回空时的 fallback
  - 确保 source_name 字段正确填充

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
from hotspot.config import (
    XHS_ACCOUNTS, perplexity_search, extract_keywords_and_topics,
    content_hash, today, yesterday, week_ago, log,
)

try:
    import httpx
except ImportError:
    os.system("pip install httpx -q")
    import httpx

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# 跨账号聚合查询 — 捕获 XHS 全平台热门话题
AGGREGATE_QUERIES = [
    "小红书 AI 一人公司 创业 最新热门笔记 今天",
    "小红书 AI Agent 工具 最新爆款笔记",
    "小红书 AI 副业 solopreneur 今日热门",
]


def _tavily_search(query: str) -> dict:
    """Tavily Search fallback when Perplexity returns empty."""
    if not TAVILY_KEY:
        log.warning("TAVILY_API_KEY not set, skipping Tavily fallback")
        return {"answer": "", "citations": []}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 10,
                    "include_answer": True,
                },
            )
            data = resp.json()
            answer = data.get("answer", "")
            results = data.get("results", [])
            # Build answer from results if no direct answer
            if not answer and results:
                answer = "\n".join(
                    f"- {r.get('title', '')}: {r.get('content', '')[:200]}"
                    for r in results[:8]
                )
            citations = [r.get("url", "") for r in results if r.get("url")]
            return {"answer": answer, "citations": citations}
    except Exception as e:
        log.error(f"Tavily search error: {e}")
        return {"answer": "", "citations": []}


def collect_xhs(incremental: bool = True) -> list[dict]:
    """
    采集小红书热点 (账号制 + 聚合查询)。
    incremental=True: 只采集昨天的内容
    incremental=False: 采集过去7天（首次运行）
    """
    log.info(f"Collecting XHS hotspots ({'incremental' if incremental else '7-day'})...")

    window_start = yesterday() if incremental else week_ago()
    window_end = today()
    all_items = []

    # === Part 1: 账号制查询 ===
    for account in XHS_ACCOUNTS:
        if incremental:
            query = (
                f"What did Xiaohongshu (小红书) creator '{account}' post yesterday ({yesterday()})? "
                f"Include post titles, key topics, likes/saves count, and approximate posting time. "
                f"Focus on AI tools, technology, indie development, and solopreneur content."
            )
        else:
            query = (
                f"What are the most popular Xiaohongshu (小红书) posts by creator '{account}' "
                f"in the past 7 days ({window_start} to {window_end})? "
                f"Include post titles, key topics, likes/saves count, and posting times."
            )

        result = perplexity_search(query)

        # Tavily fallback if Perplexity returns empty
        if not result["answer"]:
            log.info(f"  {account}: Perplexity empty, trying Tavily fallback...")
            tavily_query = f"小红书 {account} 最新笔记 AI 工具 创业"
            result = _tavily_search(tavily_query)

        if not result["answer"]:
            log.warning(f"  {account}: no data from both sources")
            continue

        items = extract_keywords_and_topics(
            f"Source: {account}\n{result['answer']}\nCitations: {', '.join(result['citations'][:3])}",
            "xhs",
        )

        for item in items:
            if not item.get("source_name") or item["source_name"] == "xhs_aggregate":
                item["source_name"] = account
            item["platform"] = "xhs"
            item["window_start"] = window_start
            item["window_end"] = window_end
            item["content_hash"] = content_hash("xhs", account, item.get("title", ""))

        all_items.extend(items)
        log.info(f"  {account}: {len(items)} items")

    # === Part 2: 跨账号聚合查询 ===
    log.info("XHS: Running aggregate queries...")
    for agg_query in AGGREGATE_QUERIES:
        result = perplexity_search(agg_query)

        # Tavily fallback
        if not result["answer"]:
            log.info(f"  Aggregate: Perplexity empty, trying Tavily...")
            result = _tavily_search(agg_query)

        if not result["answer"]:
            log.warning(f"  Aggregate query returned no data: {agg_query[:40]}...")
            continue

        items = extract_keywords_and_topics(
            f"Aggregate XHS query\n{result['answer']}\nCitations: {', '.join(result['citations'][:3])}",
            "xhs",
        )

        for item in items:
            # Keep source_name from extraction if available, else mark as aggregate
            if not item.get("source_name") or item["source_name"] == "xhs_aggregate":
                item["source_name"] = "xhs_聚合"
            item["platform"] = "xhs"
            item["window_start"] = window_start
            item["window_end"] = window_end
            item["content_hash"] = content_hash(
                "xhs", item.get("source_name", "xhs_聚合"), item.get("title", "")
            )

        all_items.extend(items)
        log.info(f"  Aggregate '{agg_query[:30]}...': {len(items)} items")

    log.info(f"XHS total: {len(all_items)} items (accounts + aggregates)")
    return all_items
