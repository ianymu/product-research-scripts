#!/usr/bin/env python3
"""
Pain Aggregator — 多痛点语义聚合成统一应用方向

基于 v7_cluster_combiner.py，但更进一步：
- 不只是聚类，而是把相关痛点聚合成「可执行的应用方向」
- 加权评分 >= 85 → TG 推送用户确认
- 输出：应用方向名 + 一句话定位 + 聚合痛点 + 加权分 + D1-D8 综合

Usage:
  python3 pain_aggregator.py --min-score 50 --threshold 85
  python3 pain_aggregator.py --dry-run  # 只导出不推送

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, TG_SHRIMPILOT_TOKEN, TG_SHRIMPILOT_CHAT_ID
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from anthropic import Anthropic
from supabase import create_client

# ── Config ──────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()

CONFIRM_THRESHOLD = 85  # 加权分 >= 此值推送 TG 确认
PAGE_SIZE = 1000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pain-aggregator")

# ── Clients ─────────────────────────────────────────────────────────────────

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Step 1: Fetch scored clusters ───────────────────────────────────────────

SELECT_COLS = (
    "cycle_id, cluster_id, cluster_label, cluster_label_en, category, "
    "total_score, outer_score, inner_score, star_rating, "
    "score_frequency, score_emotion, score_payment, score_feasibility, "
    "d1_social_contagion, d2_weak_ties, d3_identity_performance, "
    "d4_conspicuous_consumption, d5_hook_addiction, d6_nudge_designability, "
    "d7_maslow_level, d8_tech_wave, "
    "jtbd, pain_essence, current_alternatives, product_hypothesis"
)


def fetch_clusters(min_score: int) -> list[dict]:
    """Paginated fetch of all scored clusters."""
    log.info("Step 1: Fetching clusters (min_score=%d)...", min_score)
    all_rows: list[dict] = []
    page = 0
    while True:
        resp = (
            sb.table("pain_points")
            .select(SELECT_COLS)
            .eq("processed", True)
            .filter("cluster_id", "not.is", "null")
            .neq("category", "irrelevant")
            .neq("category", "noise")
            .gte("total_score", min_score)
            .order("total_score", desc=True)
            .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1

    # Group by (cycle_id, cluster_id), keep highest-score representative
    groups: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in all_rows:
        key = f"{r.get('cycle_id', 0)}_{r.get('cluster_id', 0)}"
        counts[key] = counts.get(key, 0) + 1
        if key not in groups or (r.get("total_score") or 0) > (groups[key].get("total_score") or 0):
            groups[key] = r

    clusters = list(groups.values())
    for c in clusters:
        key = f"{c.get('cycle_id', 0)}_{c.get('cluster_id', 0)}"
        c["_pain_count"] = counts.get(key, 0)

    log.info("  %d rows → %d unique clusters", len(all_rows), len(clusters))
    return clusters


# ── Step 2: LLM-driven aggregation into application directions ─────────────

def _cluster_summary(c: dict) -> str:
    label = c.get("cluster_label") or c.get("cluster_label_en") or "?"
    score = c.get("total_score") or 0
    jtbd = c.get("jtbd") or ""
    pain = c.get("pain_essence") or ""
    hypo = c.get("product_hypothesis") or ""
    d_scores = (
        f"D1:{c.get('d1_social_contagion', 0)} D2:{c.get('d2_weak_ties', 0)} "
        f"D3:{c.get('d3_identity_performance', 0)} D4:{c.get('d4_conspicuous_consumption', 0)} "
        f"D5:{c.get('d5_hook_addiction', 0)} D6:{c.get('d6_nudge_designability', 0)} "
        f"D7:{c.get('d7_maslow_level', 0)} D8:{c.get('d8_tech_wave', 0)}"
    )
    return (
        f"[{label}] (total:{score}, {d_scores})\n"
        f"  JTBD: {jtbd}\n"
        f"  Pain: {pain}\n"
        f"  Hypothesis: {hypo}"
    )


def aggregate_to_directions(clusters: list[dict]) -> list[dict]:
    """Use Claude to aggregate clusters into unified application directions."""
    log.info("Step 2: Aggregating %d clusters into application directions...", len(clusters))

    summaries = [f"{i+1}. {_cluster_summary(c)}" for i, c in enumerate(clusters)]

    # Process in chunks of 25 to avoid output truncation
    all_directions: list[dict] = []
    chunk_size = 25

    for chunk_start in range(0, len(summaries), chunk_size):
        chunk = summaries[chunk_start:chunk_start + chunk_size]
        chunk_text = "\n\n".join(chunk)

        prompt = f"""You are a product strategist analyzing startup opportunity clusters.

Below are {len(chunk)} pain point clusters with their scores and JTBD.

{chunk_text}

Your task: Aggregate these clusters into UNIFIED APPLICATION DIRECTIONS.
An application direction = a concrete product that addresses multiple related pain points.

Rules:
1. Each direction must combine 2-8 clusters that serve the SAME target user segment
2. Calculate a WEIGHTED SCORE for each direction:
   - weighted_score = (avg_total_score * 0.4) + (max_total_score * 0.3) + (cluster_count * 3) + (synergy_bonus * 0.3)
   - synergy_bonus: +5 per synergy type (functional complementarity, JTBD alignment, hook multiplication, network effect, identity coherence)
3. For each direction, synthesize a NEW combined D1-D8 score (the combined product's potential, not average)
4. Be concrete: name the product, describe what it does, who it's for

Output a JSON array of direction objects:
[{{
  "name": "product concept name",
  "one_liner": "one-sentence product pitch",
  "target_user": "specific user segment",
  "problem_statement": "what specific problem does this solve? (2-3 sentences, concrete)",
  "value_proposition": "why would users pay for this? what's the unique value?",
  "competitors": [
    {{"name": "competitor name", "weakness": "what they do poorly that we can exploit"}}
  ],
  "tam_estimate": "Total Addressable Market estimate with reasoning (e.g. '$2.1B — 15M indie devs × $140/yr avg tool spend')",
  "go_or_kill_recommendation": "GO / MAYBE / KILL with 1-sentence justification",
  "cluster_indices": [1, 3, 7],
  "weighted_score": 87.5,
  "avg_score": 72.3,
  "max_score": 85,
  "synergy_types": ["JTBD Alignment", "Hook Multiplication"],
  "combined_d_scores": {{"d1": 7, "d2": 6, "d3": 8, "d4": 5, "d5": 7, "d6": 6, "d7": 7, "d8": 6}},
  "mvp_scope": "what the 72h MVP would include (3-5 bullet points)",
  "reasoning": "why these clusters synergize into one product"
}}]

Only output valid JSON. No markdown wrapping."""

        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        try:
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            # Try to fix truncated JSON: find last complete object
            try:
                directions = json.loads(text)
            except json.JSONDecodeError:
                # Attempt to fix by finding last complete '}]' or '}\n]'
                last_bracket = text.rfind("}]")
                if last_bracket > 0:
                    text = text[:last_bracket + 2]
                    directions = json.loads(text)
                else:
                    # Try wrapping with ']'
                    last_brace = text.rfind("}")
                    if last_brace > 0:
                        text = text[:last_brace + 1] + "]"
                        directions = json.loads(text)
                    else:
                        raise

            if isinstance(directions, list):
                # Resolve cluster indices to actual data
                for d in directions:
                    resolved = []
                    for idx in d.get("cluster_indices", []):
                        actual_idx = chunk_start + idx - 1
                        if 0 <= actual_idx < len(clusters):
                            resolved.append(clusters[actual_idx])
                    d["clusters"] = resolved
                    d["cluster_count"] = len(resolved)
                all_directions.extend(directions)
                log.info("  Chunk %d: parsed %d directions", chunk_start, len(directions))
        except (json.JSONDecodeError, IndexError) as e:
            log.warning("  Chunk %d parse error: %s (text len=%d)", chunk_start, e, len(text))

        if chunk_start + chunk_size < len(summaries):
            time.sleep(1)

    # Sort by weighted_score
    all_directions.sort(key=lambda d: d.get("weighted_score", 0), reverse=True)
    log.info("  Generated %d application directions", len(all_directions))
    return all_directions


# ── Step 3: Filter and prepare for TG push ──────────────────────────────────

def filter_high_confidence(directions: list[dict], threshold: int) -> list[dict]:
    """Filter directions above the confidence threshold."""
    above = [d for d in directions if d.get("weighted_score", 0) >= threshold]
    below = [d for d in directions if d.get("weighted_score", 0) < threshold]
    log.info("  Above threshold (%d): %d directions", threshold, len(above))
    log.info("  Below threshold: %d directions", len(below))
    return above


# ── Step 4: TG notification ─────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    """Send message to Telegram."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("  TG credentials missing, skipping push")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=15)
        if resp.status_code == 200:
            log.info("  TG message sent")
            return True
        log.warning("  TG send failed: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("  TG send error: %s", e)
        return False


def push_directions_to_tg(directions: list[dict]) -> None:
    """Push high-confidence directions to TG for user confirmation."""
    if not directions:
        tg_send("🔍 *Pain Aggregator*: 本轮无 >= 85 分应用方向")
        return

    header = (
        f"🎯 *Pain Aggregator — {len(directions)} 个高置信应用方向*\n"
        f"_加权分 >= {CONFIRM_THRESHOLD}，需要你确认 GO/KILL_\n\n"
    )

    for i, d in enumerate(directions):
        d_scores = d.get("combined_d_scores", {})
        d_str = " ".join(f"D{k[-1]}:{v}" for k, v in sorted(d_scores.items()))
        rec = d.get("go_or_kill_recommendation", "")

        # Competitors summary
        comps = d.get("competitors", [])
        comp_str = " / ".join(f"{c.get('name','?')}({c.get('weakness','')[:20]})" for c in comps[:3]) if comps else "暂无"

        block = (
            f"*{i+1}. {d['name']}* (⚡ {d.get('weighted_score', 0):.0f}分) {'🟢' if 'GO' in rec.upper() else '🟡' if 'MAYBE' in rec.upper() else '🔴'}\n"
            f"📌 {d.get('one_liner', '')}\n"
            f"👤 目标用户: {d.get('target_user', '')}\n"
            f"❓ 解决什么: {d.get('problem_statement', '')[:120]}\n"
            f"💎 价值: {d.get('value_proposition', '')[:120]}\n"
            f"🏟 市场: {d.get('tam_estimate', '未评估')[:80]}\n"
            f"⚔️ 竞对: {comp_str}\n"
            f"📊 分数: 均{d.get('avg_score', 0):.0f}/最高{d.get('max_score', 0)}/聚合{d.get('cluster_count', 0)}痛点\n"
            f"🧬 {d_str}\n"
            f"🚀 MVP: {d.get('mvp_scope', '')[:150]}\n"
            f"📋 建议: {rec}\n\n"
        )
        header += block

    header += "回复 `GO 1,2` 确认 → 自动生成Demo\n`KILL 3` 放弃 | `DETAIL 1` 看详情"

    # TG has 4096 char limit, split if needed
    if len(header) <= 4096:
        tg_send(header)
    else:
        chunks = [header[i:i+4000] for i in range(0, len(header), 4000)]
        for chunk in chunks:
            tg_send(chunk)
            time.sleep(0.5)


# ── Step 5: Save report ─────────────────────────────────────────────────────

def save_report(directions: list[dict], output_path: str) -> None:
    """Save aggregation results as markdown + JSON."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# Pain Aggregator Report — Application Directions\n",
        f"Generated: {now}\n",
        f"Total directions: {len(directions)}\n",
        f"Threshold for GO recommendation: {CONFIRM_THRESHOLD}\n",
    ]

    for i, d in enumerate(directions):
        score = d.get("weighted_score", 0)
        status = "🟢 GO候选" if score >= CONFIRM_THRESHOLD else "🟡 观察"

        lines.append(f"## {i+1}. {d.get('name', 'Unnamed')} — {status} ({score:.0f}分)\n")
        lines.append(f"> {d.get('one_liner', '')}\n")
        lines.append(f"- **Target User**: {d.get('target_user', '')}")
        lines.append(f"- **Weighted Score**: {score:.1f}")
        lines.append(f"- **Avg/Max Score**: {d.get('avg_score', 0):.0f} / {d.get('max_score', 0)}")
        lines.append(f"- **Cluster Count**: {d.get('cluster_count', 0)}")
        lines.append(f"- **Synergy**: {', '.join(d.get('synergy_types', []))}")
        lines.append(f"- **MVP Scope**: {d.get('mvp_scope', '')}")
        lines.append(f"- **Reasoning**: {d.get('reasoning', '')}\n")

        d_scores = d.get("combined_d_scores", {})
        if d_scores:
            lines.append("### Combined D1-D8 Scores\n")
            lines.append("| D1 Social | D2 Weak Ties | D3 Identity | D4 Veblen | D5 Hook | D6 Nudge | D7 Maslow | D8 Tech |")
            lines.append("|-----------|-------------|-------------|-----------|---------|----------|-----------|---------|")
            lines.append(f"| {d_scores.get('d1',0)} | {d_scores.get('d2',0)} | {d_scores.get('d3',0)} | {d_scores.get('d4',0)} | {d_scores.get('d5',0)} | {d_scores.get('d6',0)} | {d_scores.get('d7',0)} | {d_scores.get('d8',0)} |\n")

        clusters = d.get("clusters", [])
        if clusters:
            lines.append("### Source Clusters\n")
            lines.append("| Cluster | Score | JTBD |")
            lines.append("|---------|-------|------|")
            for c in clusters:
                label = c.get("cluster_label") or c.get("cluster_label_en") or "?"
                cscore = c.get("total_score") or 0
                jtbd = (c.get("jtbd") or "")[:80]
                lines.append(f"| {label[:40]} | {cscore} | {jtbd} |")
            lines.append("")

    report_md = "\n".join(lines)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    # Also save JSON for programmatic access
    json_path = output_path.replace(".md", ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        # Strip cluster data to avoid circular refs
        clean = []
        for d in directions:
            cd = {k: v for k, v in d.items() if k != "clusters"}
            cd["cluster_labels"] = [
                c.get("cluster_label") or c.get("cluster_label_en") or "?"
                for c in d.get("clusters", [])
            ]
            clean.append(cd)
        json.dump(clean, f, indent=2, ensure_ascii=False)

    log.info("  Report saved: %s (%d bytes)", output_path, len(report_md))


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pain Aggregator — Cluster → Application Direction")
    parser.add_argument("--min-score", type=int, default=50, help="Min cluster total_score to include")
    parser.add_argument("--threshold", type=int, default=85, help="Weighted score threshold for GO recommendation")
    parser.add_argument("--output", type=str, default="pain-aggregation-report.md", help="Output MD path")
    parser.add_argument("--dry-run", action="store_true", help="Generate report only, no TG push")
    args = parser.parse_args()

    global CONFIRM_THRESHOLD
    CONFIRM_THRESHOLD = args.threshold

    start = time.time()
    log.info("=" * 60)
    log.info("Pain Aggregator — Starting")
    log.info("=" * 60)

    # Step 1: Fetch
    clusters = fetch_clusters(args.min_score)
    if not clusters:
        log.error("No clusters found!")
        sys.exit(1)

    # Step 2: Aggregate
    directions = aggregate_to_directions(clusters)

    # Step 3: Filter
    go_candidates = filter_high_confidence(directions, args.threshold)

    # Step 4: TG push (unless dry-run)
    if not args.dry_run:
        push_directions_to_tg(go_candidates)

    # Step 5: Save report (all directions, not just GO candidates)
    save_report(directions, args.output)

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info("Done! %d clusters → %d directions (%d GO candidates) in %.1fs",
             len(clusters), len(directions), len(go_candidates), elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
