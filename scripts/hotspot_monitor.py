#!/usr/bin/env python3
"""
hotspot_monitor.py — OpsShrimp v2 热点监测入口
全链路: 采集 → 去重 upsert → 趋势分析 → TG 摘要
WeChat 11 账号 / XHS 10 账号 / X 13 账号

运行: python3 hotspot_monitor.py [--platform wechat|xhs|x|all] [--full] [--dry-run]
  --full: 首次运行采集7天，否则默认增量(昨天)
Cron: 0 6 * * * (每天 06:00 CST)
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime

# Add scripts dir to path for package imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hotspot.config import SUPABASE_URL, SUPABASE_KEY, log
from hotspot.collectors.wechat import collect_wechat
from hotspot.collectors.xhs import collect_xhs
from hotspot.collectors.x_twitter import collect_x
from hotspot.dedup import upsert_hotspots
from hotspot.trend_analyzer import analyze_trends, save_trends
from hotspot.summary import generate_hotspot_summary
from tg_progress import TGProgress
from hotspot_monitor_tg_patch import generate_compact_tg_summary


def main():
    parser = argparse.ArgumentParser(description="OpsShrimp v2 Hotspot Monitor")
    parser.add_argument("--platform", choices=["wechat", "xhs", "x", "all"], default="all")
    parser.add_argument("--full", action="store_true", help="Collect 7 days (first run)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to Supabase")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)

    incremental = not args.full

    # Count steps based on platform selection
    steps = []
    if args.platform in ("wechat", "all"):
        steps.append(("WeChat 11 账号采集", "wechat"))
    if args.platform in ("xhs", "all"):
        steps.append(("XHS 10 账号采集", "xhs"))
    if args.platform in ("x", "all"):
        steps.append(("X 13 账号采集", "x"))
    steps.append(("去重 + 存储", None))
    steps.append(("7日趋势分析", None))
    steps.append(("生成摘要 + TG推送", None))

    progress = TGProgress("热点监测", len(steps))
    all_items = []

    try:
        # === 采集 ===
        step_idx = 0
        for step_name, platform in steps:
            if platform == "wechat":
                progress.step(step_name)
                all_items.extend(collect_wechat(incremental=incremental))
            elif platform == "xhs":
                progress.step(step_name)
                all_items.extend(collect_xhs(incremental=incremental))
            elif platform == "x":
                progress.step(step_name)
                all_items.extend(collect_x(incremental=incremental))
            elif step_name == "去重 + 存储":
                progress.step(step_name)
                if args.dry_run:
                    log.info("Dry run — skipping Supabase upsert")
                    print(json.dumps(
                        [{"title": i.get("title"), "platform": i.get("platform"), "score": i.get("hotspot_score")}
                         for i in all_items[:5]],
                        indent=2, ensure_ascii=False,
                    ))
                else:
                    upsert_hotspots(all_items)
            elif step_name == "7日趋势分析":
                progress.step(step_name)
                trends = analyze_trends()
                if not args.dry_run:
                    save_trends(trends)
            elif step_name == "生成摘要 + TG推送":
                progress.step(step_name)

        log.info(f"Total items collected: {len(all_items)}")

        if not all_items:
            log.warning("No items collected. Check API keys and network.")
            progress.fail("No items collected")
            return


        # === WeChat 全文爬取 (Task 3.7) ===
        if not args.dry_run:
            wechat_urls = [
                item.get("source_url") for item in all_items
                if item.get("platform") == "wechat"
                and item.get("source_url")
                and "mp.weixin.qq.com" in item.get("source_url", "")
            ]
            if wechat_urls:
                log.info(f"Crawling {len(wechat_urls)} WeChat articles for full content...")
                try:
                    sys.path.insert(0, os.path.expanduser("~/shrimpilot"))
                    from shrimpilot_bot import crawl_article_content
                    crawled = 0
                    for url in wechat_urls[:10]:  # Limit to 10 per run
                        result = crawl_article_content(url)
                        if result.get("success"):
                            crawled += 1
                    log.info(f"Successfully crawled {crawled}/{len(wechat_urls[:10])} articles")
                except Exception as e:
                    log.warning(f"WeChat crawl error (non-fatal): {e}")

        # === Gemini 分析 (Task 3.8) ===
        if not args.dry_run:
            try:
                from gemini_analyzer import process_articles
                log.info("Running Gemini analysis on crawled articles...")
                process_articles(limit=5, dry_run=False)
            except Exception as e:
                log.warning(f"Gemini analysis error (non-fatal): {e}")

        # === 生成摘要 ===
        trends = analyze_trends() if 'trends' not in dir() else trends
        summary = generate_hotspot_summary(
            all_items, trends=trends, incremental=incremental,
        )
        print(summary)

        # === 按 topic_cluster 聚合，丰富输出 ===
        from collections import defaultdict
        topic_groups = defaultdict(list)
        for item in sorted(all_items, key=lambda x: x.get("hotspot_score", 0), reverse=True):
            topic_groups[item.get("topic_cluster", "未分类")].append(item)

        enriched_topics = []
        for topic, articles in topic_groups.items():
            platforms = list(set(a.get("platform", "") for a in articles))
            sources = list(set(a.get("source_name", "") for a in articles if a.get("source_name")))
            enriched_topics.append({
                "topic": topic,
                "avg_score": round(sum(a.get("hotspot_score", 0) for a in articles) / len(articles), 1),
                "max_score": max(a.get("hotspot_score", 0) for a in articles),
                "article_count": len(articles),
                "platforms": platforms,
                "cross_platform_count": len(platforms),
                "blogger_count": len(sources),
                "bloggers": sources[:10],
                "articles": [
                    {
                        "source_name": a.get("source_name", ""),
                        "title": a.get("title", "")[:60],
                        "platform": a.get("platform", ""),
                        "score": a.get("hotspot_score", 0),
                        "keywords": a.get("keywords", [])[:5],
                        "source_url": a.get("source_url", ""),
                    }
                    for a in articles[:8]
                ]
            })
        enriched_topics.sort(key=lambda x: x["avg_score"], reverse=True)

        # === 保存本地摘要 (供 bot + web 读取) ===
        summary_path = os.path.expanduser("~/.shrimpilot/memory/hotspot_summary.json")
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "total_items": len(all_items),
                "incremental": incremental,
                "by_platform": {
                    p: len([i for i in all_items if i.get("platform") == p])
                    for p in ("wechat", "xhs", "x")
                },
                "summary_text": summary,
                "top_topics": enriched_topics[:20],
                "trends": [
                    {"topic": t["topic_cluster"], "type": t["trend_type"], "delta": t["score_delta"]}
                    for t in (trends or [])[:10]
                ],
            }, f, ensure_ascii=False, indent=2)

        # === 生成精简版 TG 摘要 (Top 3 + 网站链接) ===
        compact_summary = generate_compact_tg_summary(all_items, trends=trends)
        print(compact_summary)

        # 推送精简版到 TG
        from tg_progress import TGProgress as _TGP
        import urllib.request, urllib.parse
        _token = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
        _chat = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()
        if _token and _chat:
            _url = f"https://api.telegram.org/bot{_token}/sendMessage"
            _data = urllib.parse.urlencode({"chat_id": _chat, "text": compact_summary, "parse_mode": "Markdown"}).encode()
            try:
                urllib.request.urlopen(_url, _data, timeout=15)
                log.info("Compact TG summary sent")
            except Exception as e:
                log.warning("TG compact send failed: %s", e)

        progress.finish("http://18.221.160.170/shrimp/hotspot")
        log.info("Done.")

    except Exception as e:
        progress.fail(str(e))
        raise


if __name__ == "__main__":
    main()
