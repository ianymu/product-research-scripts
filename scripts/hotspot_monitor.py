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

        # === 生成摘要 ===
        trends = analyze_trends() if 'trends' not in dir() else trends
        summary = generate_hotspot_summary(
            all_items, trends=trends, incremental=incremental,
        )
        print(summary)

        # === 保存本地摘要 (供 bot 读取) ===
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
                "top_topics": [
                    {
                        "topic": item.get("topic_cluster", ""),
                        "score": item.get("hotspot_score", 0),
                        "platform": item.get("platform", ""),
                    }
                    for item in sorted(all_items, key=lambda x: x.get("hotspot_score", 0), reverse=True)[:15]
                ],
                "trends": [
                    {"topic": t["topic_cluster"], "type": t["trend_type"], "delta": t["score_delta"]}
                    for t in (trends or [])[:10]
                ],
            }, f, ensure_ascii=False, indent=2)

        progress.finish("http://18.221.160.170/hotspot-monitor.html")
        log.info("Done.")

    except Exception as e:
        progress.fail(str(e))
        raise


if __name__ == "__main__":
    main()
