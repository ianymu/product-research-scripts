#!/usr/bin/env python3
"""
V7 Cluster Combiner — Cross-cycle dedup + category discovery + combination analysis

5-step pipeline:
  1. Export: Pull all scored clusters from Supabase (paginated)
  2. Dedup: Embedding-based cross-cycle deduplication
  3. Categorize: Data-driven category discovery + batch assignment
  4. Combine: Intra-category + cross-category combination generation
  5. Report: Generate comprehensive MD report

Usage:
  python3 v7_cluster_combiner.py --min-score 50 --output ~/reports/cluster-combination-report.md

Env vars required: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY
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

import numpy as np
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity
from supabase import create_client

# ── Config ──────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()

EMBED_MODEL = "text-embedding-3-small"
LLM_MODEL = "gpt-5.4-mini"  # cheaper than gpt-5.2 for classification tasks
LLM_MODEL_SMART = "gpt-5.4-mini"  # combination reasoning

DEDUP_THRESHOLD = 0.82
PAGE_SIZE = 1000
BATCH_SIZE = 25  # clusters per LLM classification call
EMBED_BATCH = 100  # embeddings per API call

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cluster-combiner")

# ── Clients ─────────────────────────────────────────────────────────────────

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
oai = OpenAI(api_key=OPENAI_API_KEY)

# ── Step 1: Export ──────────────────────────────────────────────────────────

SELECT_COLS = (
    "cycle_id, cluster_id, cluster_label, cluster_label_en, category, "
    "total_score, outer_score, inner_score, star_rating, "
    "score_frequency, score_emotion, score_payment, score_feasibility, "
    "d1_social_contagion, d2_weak_ties, d3_identity_performance, "
    "d4_conspicuous_consumption, d5_hook_addiction, d6_nudge_designability, "
    "d7_maslow_level, d8_tech_wave, "
    "jtbd, pain_essence, current_alternatives, product_hypothesis"
)


def fetch_all_clusters(min_score: int) -> list[dict]:
    """Paginated fetch of all scored clusters, grouped by (cycle_id, cluster_id)."""
    log.info("Step 1: Fetching clusters from Supabase (min_score=%d)...", min_score)
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
        log.info("  page %d: %d rows (total so far: %d)", page, len(rows), len(all_rows))
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
        c["_key"] = key
        c["_pain_count"] = counts.get(key, 0)

    log.info("  Total rows: %d → %d unique clusters", len(all_rows), len(clusters))
    return clusters


# ── Step 2: Cross-cycle dedup ───────────────────────────────────────────────

def _embed_text(text: str) -> str:
    """Build embedding input from cluster data."""
    return text[:500]  # truncate for embedding


def get_embeddings(texts: list[str]) -> np.ndarray:
    """Batch embed texts using OpenAI."""
    all_embeds = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        resp = oai.embeddings.create(model=EMBED_MODEL, input=batch)
        all_embeds.extend([d.embedding for d in resp.data])
        if i + EMBED_BATCH < len(texts):
            time.sleep(0.2)  # rate limit courtesy
    return np.array(all_embeds)


def _llm_verify_duplicates(pairs: list[tuple[dict, dict]]) -> list[bool]:
    """Use LLM to verify if cluster pairs are truly duplicates."""
    if not pairs:
        return []

    prompt_parts = []
    for idx, (a, b) in enumerate(pairs):
        prompt_parts.append(
            f"Pair {idx+1}:\n"
            f"  A: [{a.get('cluster_label', '')}] JTBD: {a.get('jtbd', '')} | Pain: {a.get('pain_essence', '')}\n"
            f"  B: [{b.get('cluster_label', '')}] JTBD: {b.get('jtbd', '')} | Pain: {b.get('pain_essence', '')}\n"
        )

    prompt = (
        "You are deduplicating product opportunity clusters. For each pair, determine if they represent "
        "the SAME underlying user problem/opportunity (YES = duplicate, NO = different).\n"
        "Consider: same target user, same core pain, same solution space.\n"
        "Minor wording differences = YES. Different user segments or different problems = NO.\n\n"
        + "\n".join(prompt_parts) +
        "\nRespond with a JSON array of booleans, one per pair. Example: [true, false, true]\n"
        "Only output the JSON array, nothing else."
    )

    resp = oai.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=500,
    )
    text = resp.choices[0].message.content.strip()
    # Extract JSON array
    try:
        # Handle potential markdown wrapping
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        result = json.loads(text)
        if isinstance(result, list) and len(result) == len(pairs):
            return [bool(x) for x in result]
    except (json.JSONDecodeError, IndexError):
        pass
    # Fallback: assume all are duplicates (conservative)
    log.warning("  LLM verification parse failed, falling back to embedding-only dedup")
    return [True] * len(pairs)


def dedup_clusters(clusters: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    """
    Deduplicate clusters using embeddings + LLM verification.
    Returns: (deduped_clusters, merge_groups)
    """
    log.info("Step 2: Cross-cycle deduplication (%d clusters)...", len(clusters))

    if len(clusters) <= 1:
        return clusters, [[c] for c in clusters]

    # Build embedding texts
    texts = []
    for c in clusters:
        label = c.get("cluster_label") or c.get("cluster_label_en") or ""
        jtbd = c.get("jtbd") or ""
        pain = c.get("pain_essence") or ""
        texts.append(_embed_text(f"{label}: {jtbd} | {pain}"))

    log.info("  Generating embeddings...")
    embeddings = get_embeddings(texts)

    # Compute similarity matrix
    log.info("  Computing similarity matrix...")
    sim_matrix = cosine_similarity(embeddings)

    # Find potential duplicate pairs (above threshold)
    n = len(clusters)
    candidate_pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i][j] > DEDUP_THRESHOLD:
                candidate_pairs.append((i, j, sim_matrix[i][j]))

    log.info("  Found %d candidate duplicate pairs (threshold=%.2f)", len(candidate_pairs), DEDUP_THRESHOLD)

    if not candidate_pairs:
        return clusters, [[c] for c in clusters]

    # LLM verification in batches of 20
    verified_pairs: list[tuple[int, int]] = []
    for batch_start in range(0, len(candidate_pairs), 20):
        batch = candidate_pairs[batch_start:batch_start + 20]
        pair_data = [(clusters[i], clusters[j]) for i, j, _ in batch]
        results = _llm_verify_duplicates(pair_data)
        for k, is_dup in enumerate(results):
            if is_dup:
                verified_pairs.append((batch[k][0], batch[k][1]))

    log.info("  LLM verified %d duplicate pairs", len(verified_pairs))

    # Union-Find to merge clusters
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, j in verified_pairs:
        union(i, j)

    # Build merge groups
    group_map: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        group_map.setdefault(root, []).append(i)

    merge_groups: list[list[dict]] = []
    deduped: list[dict] = []
    for indices in group_map.values():
        group = [clusters[i] for i in indices]
        # Keep highest-score representative
        best = max(group, key=lambda c: c.get("total_score") or 0)
        best["_merged_count"] = len(group)
        best["_merged_labels"] = [c.get("cluster_label", "") for c in group if c["_key"] != best["_key"]]
        deduped.append(best)
        merge_groups.append(group)

    log.info("  Deduplication: %d → %d unique themes", len(clusters), len(deduped))
    return deduped, merge_groups


# ── Step 3: Category discovery + assignment ─────────────────────────────────

def discover_categories(clusters: list[dict]) -> list[str]:
    """Round 1: Let LLM discover natural categories from data."""
    log.info("Step 3a: Discovering categories from data...")

    # Build cluster summary for LLM
    summaries = []
    for i, c in enumerate(clusters):
        label = c.get("cluster_label") or c.get("cluster_label_en") or f"cluster-{i}"
        jtbd = c.get("jtbd") or ""
        cat = c.get("category") or ""
        score = c.get("total_score") or 0
        summaries.append(f"{i+1}. [{label}] (score:{score}, cat:{cat}) JTBD: {jtbd}")

    # Chunk if too many (max ~100 per call for context)
    all_summaries = "\n".join(summaries)

    prompt = (
        "You are analyzing product opportunity clusters from a startup research pipeline.\n"
        "Below are all unique clusters with their labels, scores, and JTBD (Jobs To Be Done).\n\n"
        f"{all_summaries}\n\n"
        "Based on this data, propose a set of PRODUCT CATEGORIES that naturally emerge.\n"
        "Guidelines:\n"
        "- Use real market categories (like App Store categories, SaaS verticals, consumer segments)\n"
        "- Each category should have at least 2-3 clusters\n"
        "- Aim for 10-25 categories total\n"
        "- Category names should be concise (2-4 words max)\n"
        "- Cover the full spectrum: Social, Health & Wellness, Finance & Fintech, Dating & Relationships, "
        "Entertainment, Habit & Behavior, Creator Tools, Writing & Content, Marketing & Growth, "
        "Developer Tools, Productivity & Office, AI Automation, Education & Learning, E-commerce, "
        "Travel, Food & Dining, Real Estate, Legal, Recruiting & HR, Parenting, Pet Care, Sports & Fitness, "
        "Music & Audio, Photo & Video, Fashion & Style, Communication, Security & Privacy, Smart Home, "
        "Healthcare, Environment, Gaming, Supply Chain, etc.\n"
        "- Only create categories that have actual clusters to fill them\n\n"
        "Output a JSON array of category name strings. Only output the JSON array.\n"
        'Example: ["AI Coding Tools", "Mental Health", "Creator Economy", ...]'
    )

    resp = oai.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    text = resp.choices[0].message.content.strip()

    try:
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        categories = json.loads(text)
        if isinstance(categories, list):
            log.info("  Discovered %d categories: %s", len(categories), categories)
            return categories
    except (json.JSONDecodeError, IndexError):
        pass

    log.warning("  Category discovery failed, using fallback categories")
    return [
        "AI & Automation", "Developer Tools", "Productivity", "Social & Community",
        "Health & Wellness", "Finance & Fintech", "Creator Tools", "Education",
        "Marketing & Growth", "E-commerce", "Communication", "Entertainment",
        "Dating & Relationships", "Other"
    ]


def assign_categories(clusters: list[dict], categories: list[str]) -> list[dict]:
    """Round 2: Batch-assign categories to each cluster."""
    log.info("Step 3b: Assigning categories to %d clusters (batch=%d)...", len(clusters), BATCH_SIZE)

    cat_list_str = json.dumps(categories)

    for batch_start in range(0, len(clusters), BATCH_SIZE):
        batch = clusters[batch_start:batch_start + BATCH_SIZE]
        batch_desc = []
        for i, c in enumerate(batch):
            label = c.get("cluster_label") or c.get("cluster_label_en") or ""
            jtbd = c.get("jtbd") or ""
            cat = c.get("category") or ""
            batch_desc.append(
                f'{{"idx":{batch_start + i},"label":"{label}","jtbd":"{jtbd}","original_cat":"{cat}"}}'
            )

        prompt = (
            f"Categories: {cat_list_str}\n\n"
            "Assign each cluster below to 1 primary category and optionally 1 secondary category.\n"
            "Clusters:\n" + "\n".join(batch_desc) + "\n\n"
            'Output a JSON array of objects: [{"idx": 0, "primary": "...", "secondary": "..."}, ...]\n'
            "secondary can be null if no good fit. Only output the JSON array."
        )

        resp = oai.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=3000,
        )
        text = resp.choices[0].message.content.strip()

        try:
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            assignments = json.loads(text)
            for a in assignments:
                idx = a.get("idx", -1)
                if 0 <= idx < len(clusters):
                    clusters[idx]["_primary_cat"] = a.get("primary", "Other")
                    clusters[idx]["_secondary_cat"] = a.get("secondary")
        except (json.JSONDecodeError, IndexError) as e:
            log.warning("  Batch %d parse error: %s", batch_start, e)
            for c in batch:
                c.setdefault("_primary_cat", c.get("category", "Other"))

        if batch_start + BATCH_SIZE < len(clusters):
            time.sleep(0.3)

    # Ensure all have categories
    for c in clusters:
        c.setdefault("_primary_cat", c.get("category", "Other"))

    # Log distribution
    cat_counts: dict[str, int] = {}
    for c in clusters:
        cat = c["_primary_cat"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    log.info("  Category distribution: %s", dict(sorted(cat_counts.items(), key=lambda x: -x[1])))

    return clusters


# ── Step 4: Combination generation ──────────────────────────────────────────

def _build_cluster_summary(c: dict) -> str:
    """Build a concise summary string for a cluster."""
    label = c.get("cluster_label") or c.get("cluster_label_en") or ""
    score = c.get("total_score") or 0
    jtbd = c.get("jtbd") or ""
    pain = c.get("pain_essence") or ""
    d1 = c.get("d1_social_contagion") or 0
    d2 = c.get("d2_weak_ties") or 0
    d3 = c.get("d3_identity_performance") or 0
    d5 = c.get("d5_hook_addiction") or 0
    return (
        f"[{label}] (score:{score}, D1:{d1}/8, D2:{d2}/7, D3:{d3}/8, D5:{d5}/8)\n"
        f"  JTBD: {jtbd}\n"
        f"  Pain: {pain}"
    )


def generate_combinations_for_category(cat_name: str, cat_clusters: list[dict]) -> list[dict]:
    """Phase 4A: Generate combinations within a category."""
    if len(cat_clusters) < 2:
        return []

    summaries = [f"{i+1}. {_build_cluster_summary(c)}" for i, c in enumerate(cat_clusters)]

    prompt = (
        f'Category: "{cat_name}" — {len(cat_clusters)} clusters\n\n'
        + "\n".join(summaries) + "\n\n"
        "Identify groups of 2-8 clusters that can be COMBINED into a single product.\n"
        "Combination criteria (at least 2 must apply):\n"
        "1. Functional complementarity (A's output feeds B's input)\n"
        "2. JTBD alignment (same user, same workflow)\n"
        "3. Hook multiplication (combined D5/Hook score would be higher)\n"
        "4. Network effect amplification (social layer + tool layer, D1/D2 boost)\n"
        "5. Identity coherence (stronger identity label when combined, D3 boost)\n\n"
        "For each combination, provide:\n"
        "- name: product concept name (concise, memorable)\n"
        "- one_liner: one-sentence product pitch\n"
        "- cluster_indices: array of cluster numbers (1-based from list above)\n"
        "- synergy_types: which criteria apply (array of numbers 1-5)\n"
        "- reasoning: 1-2 sentences on why these clusters synergize\n\n"
        "Rules:\n"
        "- A cluster can appear in multiple combinations\n"
        "- Prefer including at least one 4+ star cluster per combination\n"
        "- Don't force combinations that don't make sense\n"
        "- If no good combinations exist, return empty array\n\n"
        "Output a JSON array of combination objects. Only output the JSON array."
    )

    resp = oai.chat.completions.create(
        model=LLM_MODEL_SMART,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=4000,
    )
    text = resp.choices[0].message.content.strip()

    try:
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        combos = json.loads(text)
        if not isinstance(combos, list):
            combos = []
    except (json.JSONDecodeError, IndexError):
        log.warning("  Combination parse failed for %s", cat_name)
        combos = []

    # Resolve indices to actual cluster data
    resolved = []
    for combo in combos:
        indices = combo.get("cluster_indices", [])
        combo_clusters = []
        for idx in indices:
            # 1-based index
            if 1 <= idx <= len(cat_clusters):
                combo_clusters.append(cat_clusters[idx - 1])
        if len(combo_clusters) >= 2:
            avg_score = sum(c.get("total_score", 0) for c in combo_clusters) / len(combo_clusters)
            max_score = max(c.get("total_score", 0) for c in combo_clusters)
            resolved.append({
                "name": combo.get("name", "Unnamed"),
                "one_liner": combo.get("one_liner", ""),
                "category": cat_name,
                "clusters": combo_clusters,
                "synergy_types": combo.get("synergy_types", []),
                "reasoning": combo.get("reasoning", ""),
                "avg_score": round(avg_score, 1),
                "max_score": max_score,
                "cluster_count": len(combo_clusters),
            })

    return resolved


def generate_cross_category_combinations(
    clusters: list[dict], intra_combos: list[dict]
) -> list[dict]:
    """Phase 4B: Cross-category combination scan."""
    log.info("Step 4b: Cross-category combination scan...")

    # Find clusters not yet in any intra-category combination
    used_keys = set()
    for combo in intra_combos:
        for c in combo["clusters"]:
            used_keys.add(c["_key"])

    unused = [c for c in clusters if c["_key"] not in used_keys and (c.get("total_score") or 0) >= 55]
    # Also include high-scoring used ones for cross-category
    high_score_any = [c for c in clusters if (c.get("total_score") or 0) >= 65]
    candidates = list({c["_key"]: c for c in unused + high_score_any}.values())

    if len(candidates) < 3:
        log.info("  Too few candidates for cross-category (%d)", len(candidates))
        return []

    # Group by secondary category for cross-pollination
    summaries = []
    for i, c in enumerate(candidates[:60]):  # limit to 60 for context
        cat1 = c.get("_primary_cat", "?")
        cat2 = c.get("_secondary_cat") or ""
        summaries.append(
            f"{i+1}. [{cat1}" + (f"/{cat2}" if cat2 else "") + "] "
            + _build_cluster_summary(c)
        )

    prompt = (
        "These clusters span DIFFERENT categories. Find cross-category product opportunities.\n"
        "Examples: health + social = health community app, finance + AI = AI financial advisor\n\n"
        + "\n".join(summaries) + "\n\n"
        "Rules: 2-6 clusters per combination, must span at least 2 categories.\n"
        "Same output format as before: JSON array of {name, one_liner, cluster_indices, synergy_types, reasoning}.\n"
        "Only output the JSON array."
    )

    resp = oai.chat.completions.create(
        model=LLM_MODEL_SMART,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=4000,
    )
    text = resp.choices[0].message.content.strip()

    try:
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        combos = json.loads(text)
        if not isinstance(combos, list):
            combos = []
    except (json.JSONDecodeError, IndexError):
        log.warning("  Cross-category parse failed")
        combos = []

    resolved = []
    for combo in combos:
        indices = combo.get("cluster_indices", [])
        combo_clusters = []
        for idx in indices:
            if 1 <= idx <= len(candidates):
                combo_clusters.append(candidates[idx - 1])
        if len(combo_clusters) >= 2:
            cats = set(c.get("_primary_cat", "?") for c in combo_clusters)
            if len(cats) >= 2:
                avg_score = sum(c.get("total_score", 0) for c in combo_clusters) / len(combo_clusters)
                resolved.append({
                    "name": combo.get("name", "Unnamed"),
                    "one_liner": combo.get("one_liner", ""),
                    "category": "Cross-Category: " + " + ".join(sorted(cats)),
                    "clusters": combo_clusters,
                    "synergy_types": combo.get("synergy_types", []),
                    "reasoning": combo.get("reasoning", ""),
                    "avg_score": round(avg_score, 1),
                    "max_score": max(c.get("total_score", 0) for c in combo_clusters),
                    "cluster_count": len(combo_clusters),
                    "is_cross_category": True,
                })

    log.info("  Found %d cross-category combinations", len(resolved))
    return resolved


def find_standalone_opportunities(
    clusters: list[dict], all_combos: list[dict], min_score: int = 60
) -> list[dict]:
    """Phase 4C: Identify high-scoring clusters not in any combination."""
    used_keys = set()
    for combo in all_combos:
        for c in combo["clusters"]:
            used_keys.add(c["_key"])

    standalone = [
        c for c in clusters
        if c["_key"] not in used_keys and (c.get("total_score") or 0) >= min_score
    ]
    standalone.sort(key=lambda c: c.get("total_score", 0), reverse=True)
    log.info("  Standalone opportunities (score >= %d, not in combos): %d", min_score, len(standalone))
    return standalone


# ── Step 5: Report generation ───────────────────────────────────────────────

SYNERGY_LABELS = {
    1: "Functional Complementarity",
    2: "JTBD Alignment",
    3: "Hook Multiplication",
    4: "Network Effect Amplification",
    5: "Identity Coherence",
}

STAR_EMOJI = {5: "5-Star", 4: "4-Star", 3: "3-Star", 0: "Below 3"}


def _cluster_row(c: dict) -> str:
    """Format a cluster as a markdown table row."""
    label = c.get("cluster_label") or c.get("cluster_label_en") or "?"
    score = c.get("total_score") or 0
    star = c.get("star_rating") or 0
    cycle = c.get("cycle_id") or 0
    cid = c.get("cluster_id") or 0
    d5 = c.get("d5_hook_addiction") or 0
    d1 = c.get("d1_social_contagion") or 0
    jtbd = (c.get("jtbd") or "")[:60]
    return f"| {label[:40]} | {score} | {star} | C{cycle}#{cid} | D1:{d1} D5:{d5} | {jtbd} |"


def generate_report(
    raw_count: int,
    deduped: list[dict],
    merge_groups: list[list[dict]],
    categories: list[str],
    intra_combos: list[dict],
    cross_combos: list[dict],
    standalone: list[dict],
    min_score: int,
) -> str:
    """Generate the full markdown report."""
    log.info("Step 5: Generating report...")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    all_combos = intra_combos + cross_combos

    # Sort combos by max_score
    all_combos_sorted = sorted(all_combos, key=lambda x: x["max_score"], reverse=True)
    top5 = all_combos_sorted[:5]

    # Category distribution
    cat_counts: dict[str, dict] = {}
    for c in deduped:
        cat = c.get("_primary_cat", "Other")
        if cat not in cat_counts:
            cat_counts[cat] = {"count": 0, "top_score": 0, "combos": 0}
        cat_counts[cat]["count"] += 1
        if (c.get("total_score") or 0) > cat_counts[cat]["top_score"]:
            cat_counts[cat]["top_score"] = c.get("total_score") or 0

    for combo in intra_combos:
        cat = combo["category"]
        if cat in cat_counts:
            cat_counts[cat]["combos"] += 1

    lines = []
    lines.append(f"# V7 Cluster Combination Analysis Report\n")
    lines.append(f"Generated: {now}")
    lines.append(f"Min score threshold: {min_score}\n")

    # ── 1. Summary ──
    lines.append("## 1. Executive Summary\n")
    lines.append(f"- **Raw clusters** (cycle_id × cluster_id): {raw_count}")
    lines.append(f"- **After deduplication**: {len(deduped)} unique themes")
    merged_count = sum(1 for g in merge_groups if len(g) > 1)
    lines.append(f"- **Merged groups**: {merged_count} (cross-cycle duplicates removed)")
    lines.append(f"- **Product categories**: {len(categories)}")
    lines.append(f"- **Intra-category combinations**: {len(intra_combos)}")
    lines.append(f"- **Cross-category combinations**: {len(cross_combos)}")
    lines.append(f"- **Standalone opportunities** (>= 60, not combined): {len(standalone)}")
    lines.append(f"- **Total product directions**: {len(all_combos) + len(standalone)}\n")

    if top5:
        lines.append("### Top 5 Product Directions\n")
        lines.append("| # | Name | Category | Clusters | Max Score | Synergy |")
        lines.append("|---|------|----------|----------|-----------|---------|")
        for i, combo in enumerate(top5):
            syn = ", ".join(SYNERGY_LABELS.get(s, str(s)) for s in combo.get("synergy_types", []))
            lines.append(
                f"| {i+1} | **{combo['name']}** | {combo['category']} | "
                f"{combo['cluster_count']} | {combo['max_score']} | {syn} |"
            )
        lines.append("")

    # ── 2. Dedup results ──
    lines.append("## 2. Deduplication Results\n")
    if merged_count > 0:
        lines.append(f"Found {merged_count} merge groups where clusters from different cycles represent the same theme:\n")
        for group in merge_groups:
            if len(group) > 1:
                best = max(group, key=lambda c: c.get("total_score") or 0)
                label = best.get("cluster_label") or "?"
                lines.append(f"- **{label}** (score: {best.get('total_score', 0)})")
                for c in group:
                    if c["_key"] != best["_key"]:
                        lines.append(
                            f"  - Merged: [{c.get('cluster_label', '?')}] "
                            f"(C{c.get('cycle_id', 0)}#{c.get('cluster_id', 0)}, "
                            f"score: {c.get('total_score', 0)})"
                        )
        lines.append("")
    else:
        lines.append("No cross-cycle duplicates found.\n")

    # ── 3. Category distribution ──
    lines.append("## 3. Category Distribution\n")
    lines.append("| Category | Clusters | Top Score | Combinations |")
    lines.append("|----------|----------|-----------|-------------|")
    for cat, info in sorted(cat_counts.items(), key=lambda x: -x[1]["count"]):
        lines.append(f"| {cat} | {info['count']} | {info['top_score']} | {info['combos']} |")
    lines.append("")

    # ── 4. Intra-category combinations ──
    lines.append("## 4. Intra-Category Combinations\n")

    combos_by_cat: dict[str, list[dict]] = {}
    for combo in intra_combos:
        combos_by_cat.setdefault(combo["category"], []).append(combo)

    for cat in sorted(combos_by_cat.keys()):
        cat_combos_list = sorted(combos_by_cat[cat], key=lambda x: -x["max_score"])
        lines.append(f"### {cat} ({len(cat_combos_list)} combinations)\n")

        for combo in cat_combos_list:
            syn = ", ".join(SYNERGY_LABELS.get(s, str(s)) for s in combo.get("synergy_types", []))
            lines.append(f"#### {combo['name']}\n")
            lines.append(f"> {combo['one_liner']}\n")
            lines.append(f"- **Synergy**: {syn}")
            lines.append(f"- **Avg Score**: {combo['avg_score']} | **Max Score**: {combo['max_score']}")
            lines.append(f"- **Reasoning**: {combo['reasoning']}\n")
            lines.append("| Cluster | Score | Star | ID | Highlights | JTBD |")
            lines.append("|---------|-------|------|----|------------|------|")
            for c in combo["clusters"]:
                lines.append(_cluster_row(c))
            lines.append("")

    # ── 5. Cross-category combinations ──
    lines.append("## 5. Cross-Category Combinations\n")
    if cross_combos:
        for combo in sorted(cross_combos, key=lambda x: -x["max_score"]):
            syn = ", ".join(SYNERGY_LABELS.get(s, str(s)) for s in combo.get("synergy_types", []))
            lines.append(f"#### {combo['name']}\n")
            lines.append(f"> {combo['one_liner']}\n")
            lines.append(f"- **Categories**: {combo['category']}")
            lines.append(f"- **Synergy**: {syn}")
            lines.append(f"- **Avg Score**: {combo['avg_score']} | **Max Score**: {combo['max_score']}")
            lines.append(f"- **Reasoning**: {combo['reasoning']}\n")
            lines.append("| Cluster | Score | Star | ID | Highlights | JTBD |")
            lines.append("|---------|-------|------|----|------------|------|")
            for c in combo["clusters"]:
                lines.append(_cluster_row(c))
            lines.append("")
    else:
        lines.append("No cross-category combinations identified.\n")

    # ── 6. Standalone opportunities ──
    lines.append("## 6. Standalone Opportunities (>= 60, Not Combined)\n")
    if standalone:
        lines.append("| Cluster | Score | Star | Category | ID | JTBD |")
        lines.append("|---------|-------|------|----------|----|------|")
        for c in standalone:
            label = c.get("cluster_label") or c.get("cluster_label_en") or "?"
            score = c.get("total_score") or 0
            star = c.get("star_rating") or 0
            cat = c.get("_primary_cat") or "?"
            cycle = c.get("cycle_id") or 0
            cid = c.get("cluster_id") or 0
            jtbd = (c.get("jtbd") or "")[:60]
            lines.append(f"| {label[:40]} | {score} | {star} | {cat} | C{cycle}#{cid} | {jtbd} |")
        lines.append("")
    else:
        lines.append("All clusters >= 60 have been included in combinations.\n")

    # ── 7. Appendix: Full deduped cluster table ──
    lines.append("## 7. Appendix: Full Deduped Cluster Table\n")
    lines.append(f"Total: {len(deduped)} unique clusters\n")
    lines.append("| # | Cluster | Score | Star | Category | Cycle#ID | Pain Count | JTBD |")
    lines.append("|---|---------|-------|------|----------|----------|------------|------|")
    deduped_sorted = sorted(deduped, key=lambda c: c.get("total_score", 0), reverse=True)
    for i, c in enumerate(deduped_sorted):
        label = c.get("cluster_label") or c.get("cluster_label_en") or "?"
        score = c.get("total_score") or 0
        star = c.get("star_rating") or 0
        cat = c.get("_primary_cat") or c.get("category") or "?"
        cycle = c.get("cycle_id") or 0
        cid = c.get("cluster_id") or 0
        pcount = c.get("_pain_count") or 0
        jtbd = (c.get("jtbd") or "")[:50]
        merged = c.get("_merged_count", 1)
        merged_note = f" (+{merged-1} merged)" if merged > 1 else ""
        lines.append(
            f"| {i+1} | {label[:35]}{merged_note} | {score} | {star} | {cat} | C{cycle}#{cid} | {pcount} | {jtbd} |"
        )
    lines.append("")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="V7 Cluster Combiner")
    parser.add_argument("--min-score", type=int, default=50, help="Minimum total_score threshold")
    parser.add_argument("--output", type=str, default="cluster-combination-report.md", help="Output MD file path")
    parser.add_argument("--skip-dedup", action="store_true", help="Skip dedup step (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Export only, no LLM calls")
    args = parser.parse_args()

    start = time.time()
    log.info("=" * 60)
    log.info("V7 Cluster Combiner — Starting")
    log.info("=" * 60)

    # Step 1: Export
    clusters = fetch_all_clusters(args.min_score)
    raw_count = len(clusters)
    if not clusters:
        log.error("No clusters found! Check Supabase data.")
        sys.exit(1)

    if args.dry_run:
        log.info("Dry run — writing raw export and exiting")
        with open(args.output.replace(".md", "-raw.json"), "w") as f:
            json.dump(clusters, f, indent=2, default=str)
        return

    # Step 2: Dedup
    if args.skip_dedup:
        deduped = clusters
        merge_groups = [[c] for c in clusters]
    else:
        deduped, merge_groups = dedup_clusters(clusters)

    # Step 3: Categorize
    categories = discover_categories(deduped)
    deduped = assign_categories(deduped, categories)

    # Step 4: Combine
    log.info("Step 4a: Intra-category combinations...")
    # Group by primary category
    cat_groups: dict[str, list[dict]] = {}
    for c in deduped:
        cat = c.get("_primary_cat", "Other")
        cat_groups.setdefault(cat, []).append(c)

    intra_combos: list[dict] = []
    for cat_name, cat_clusters in sorted(cat_groups.items()):
        if len(cat_clusters) >= 2:
            log.info("  Processing %s (%d clusters)...", cat_name, len(cat_clusters))
            combos = generate_combinations_for_category(cat_name, cat_clusters)
            intra_combos.extend(combos)
            log.info("    → %d combinations", len(combos))
            time.sleep(0.3)

    log.info("  Total intra-category combinations: %d", len(intra_combos))

    # Phase 4B: Cross-category
    cross_combos = generate_cross_category_combinations(deduped, intra_combos)

    # Phase 4C: Standalone
    all_combos = intra_combos + cross_combos
    standalone = find_standalone_opportunities(deduped, all_combos, min_score=60)

    # Step 5: Report
    report = generate_report(
        raw_count=raw_count,
        deduped=deduped,
        merge_groups=merge_groups,
        categories=categories,
        intra_combos=intra_combos,
        cross_combos=cross_combos,
        standalone=standalone,
        min_score=args.min_score,
    )

    # Ensure output directory exists
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info("Done! Report: %s (%d bytes, %.1f sec)", args.output, len(report), elapsed)
    log.info("  Clusters: %d raw → %d deduped → %d combos + %d standalone",
             raw_count, len(deduped), len(all_combos), len(standalone))
    log.info("=" * 60)


if __name__ == "__main__":
    main()
