#!/usr/bin/env python3
"""
X/Twitter 热点采集器 — 13 个对标账号
采集方式: Perplexity 搜索 → Claude 提取结构化数据
"""
from hotspot.config import (
    X_ACCOUNTS, perplexity_search, extract_keywords_and_topics,
    content_hash, today, yesterday, week_ago, log,
)


def collect_x(incremental: bool = True) -> list[dict]:
    """
    采集 X/Twitter 热点。
    Batch 5 accounts per query for efficiency.
    """
    log.info(f"Collecting X/Twitter hotspots ({'incremental' if incremental else '7-day'})...")

    window_start = yesterday() if incremental else week_ago()
    window_end = today()
    all_items = []

    batch_size = 5
    for i in range(0, len(X_ACCOUNTS), batch_size):
        batch = X_ACCOUNTS[i:i + batch_size]
        accounts_str = ", ".join(f"@{a}" for a in batch)

        if incremental:
            query = (
                f"What were the most discussed tweets from {accounts_str} on X/Twitter "
                f"yesterday ({yesterday()})? "
                f"Focus on: startups, indie hacking, AI tools, solopreneur, product building. "
                f"Include tweet content summary, engagement metrics, and posting time."
            )
        else:
            query = (
                f"What are the most discussed tweets from {accounts_str} on X/Twitter "
                f"in the past 7 days ({window_start} to {window_end})? "
                f"Focus on: startups, indie hacking, AI tools, solopreneur, product building. "
                f"Include tweet content summary, engagement metrics, and posting times."
            )

        result = perplexity_search(query)
        if not result["answer"]:
            log.warning(f"  Batch {accounts_str}: no data returned")
            continue

        items = extract_keywords_and_topics(
            f"Accounts: {accounts_str}\n{result['answer']}\nCitations: {', '.join(result['citations'][:3])}",
            "x",
        )

        for item in items:
            item["platform"] = "x"
            item["window_start"] = window_start
            item["window_end"] = window_end
            item["content_hash"] = content_hash(
                "x", item.get("source_name", "x_batch"), item.get("title", "")
            )

        all_items.extend(items)
        log.info(f"  Batch {accounts_str}: {len(items)} items")

    return all_items
