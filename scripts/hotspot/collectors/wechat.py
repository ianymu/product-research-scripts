#!/usr/bin/env python3
"""
WeChat 公众号热点采集器 — 11 个对标账号
采集方式: Perplexity 搜索 → Claude 提取结构化数据
"""
from hotspot.config import (
    WECHAT_ACCOUNTS, perplexity_search, extract_keywords_and_topics,
    content_hash, today, yesterday, week_ago, log,
)


def collect_wechat(incremental: bool = True) -> list[dict]:
    """
    采集微信公众号热点。
    incremental=True: 只采集昨天的内容
    incremental=False: 采集过去7天（首次运行）
    """
    log.info(f"Collecting WeChat hotspots ({'incremental' if incremental else '7-day'})...")

    window_start = yesterday() if incremental else week_ago()
    window_end = today()
    all_items = []

    for blogger in WECHAT_ACCOUNTS:
        if incremental:
            query = (
                f"What articles did WeChat public account '{blogger}' (微信公众号) "
                f"publish yesterday ({yesterday()})? "
                f"List article titles, key topics, estimated engagement, and approximate publish time. "
                f"Focus on AI, technology, startups, and solopreneur content."
            )
        else:
            query = (
                f"What are the most popular articles published by WeChat public account "
                f"'{blogger}' (微信公众号) in the past 7 days ({window_start} to {window_end})? "
                f"List article titles, key topics, estimated engagement, and publish times. "
                f"Focus on AI, technology, startups, and solopreneur content."
            )

        result = perplexity_search(query)
        if not result["answer"]:
            log.warning(f"  {blogger}: no data returned")
            continue

        items = extract_keywords_and_topics(
            f"Source: {blogger}\n{result['answer']}\nCitations: {', '.join(result['citations'][:3])}",
            "wechat",
        )

        for item in items:
            if not item.get("source_name") or item["source_name"] == "wechat_aggregate":
                item["source_name"] = blogger
            item["platform"] = "wechat"
            item["window_start"] = window_start
            item["window_end"] = window_end
            item["content_hash"] = content_hash("wechat", blogger, item.get("title", ""))

        all_items.extend(items)
        log.info(f"  {blogger}: {len(items)} items")

    # 跨账号趋势查询
    cross_query = (
        f"What are the top trending AI and technology topics on Chinese WeChat public accounts "
        f"(微信公众号) {'yesterday' if incremental else 'in the past 7 days'} "
        f"({window_start} to {window_end})? "
        f"Focus on: AI agents, AI programming, solopreneur tools, startup trends. "
        f"Include which accounts posted them and estimated publish times."
    )
    result = perplexity_search(cross_query)
    if result["answer"]:
        items = extract_keywords_and_topics(result["answer"], "wechat")
        for item in items:
            item["platform"] = "wechat"
            item["window_start"] = window_start
            item["window_end"] = window_end
            item["content_hash"] = content_hash(
                "wechat", item.get("source_name", "cross"), item.get("title", "")
            )
        all_items.extend(items)
        log.info(f"  Cross-blogger trending: {len(items)} items")

    return all_items
