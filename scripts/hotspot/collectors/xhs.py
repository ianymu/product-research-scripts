#!/usr/bin/env python3
"""
小红书热点采集器 — 10 个对标账号 (账号制)
采集方式: Perplexity 搜索 → Claude 提取结构化数据
"""
from hotspot.config import (
    XHS_ACCOUNTS, perplexity_search, extract_keywords_and_topics,
    content_hash, today, yesterday, week_ago, log,
)


def collect_xhs(incremental: bool = True) -> list[dict]:
    """
    采集小红书热点 (账号制，非话题制)。
    incremental=True: 只采集昨天的内容
    incremental=False: 采集过去7天（首次运行）
    """
    log.info(f"Collecting XHS hotspots ({'incremental' if incremental else '7-day'})...")

    window_start = yesterday() if incremental else week_ago()
    window_end = today()
    all_items = []

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
        if not result["answer"]:
            log.warning(f"  {account}: no data returned")
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

    return all_items
