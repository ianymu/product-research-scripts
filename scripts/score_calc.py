"""
V7 Pipeline — Score Calculator with D1-D8
Dual-layer scoring: Outer (40) + Inner D1-D8 (60) = 100 total.
Uses Opus model for deep reasoning.
"""
import os
import json
import sys
import anthropic
from supabase import create_client

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SCORING_PROMPT = """You are an expert product evaluator. Score this pain point cluster using the V7 dual-layer framework.

## Cluster: {cluster_label}
## Representative pain points:
{samples}

## Scoring Framework

### Layer 1: Outer Functional Fundamentals (40 points)
- frequency /10: How often this pain point appears and scenario density
- emotion /10: Emotional intensity (anger/anxiety/shame/desire)
- payment /10: Willingness to pay and alternative solution costs
- feasibility /10: Can an MVP be built and validated in 72 hours?

### Layer 2: Inner Viral Academic Score (60 points)
- D1 social_contagion /8: Three-degree influence theory. 8=TikTok-level viral; 1=pure backend tool
- D2 weak_ties /7: Complex contagion + cross-circle spread. 7=universally understood; 1=extremely niche
- D3 identity_performance /8: Impression management. 8=identity statement; 1=pure utility
- D4 conspicuous_consumption /7: Veblen effect. 7=Pro Badge social currency; 1=no display value
- D5 hook_addiction /8: Trigger→Action→Variable Reward→Investment. 8=daily anxiety; 1=use and forget
- D6 nudge_designability /7: Default/anchoring/loss aversion/social proof. 7=every node nudgeable; 1=no space
- D7 maslow_level /8: Higher = higher LTV. 8=self-actualization; 1=physiological
- D8 tech_wave /7: Perez+Gartner. 7=deployment sweet spot; 1=too early or red ocean

For EACH dimension output:
1. Score (integer)
2. Reasoning (2-3 sentences)
3. Testable Hypothesis ("If... then...")
4. Analogy Case (real product comparison)

Respond in this exact JSON format:
{{
  "frequency": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "emotion": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "payment": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "feasibility": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d1_social_contagion": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d2_weak_ties": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d3_identity_performance": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d4_conspicuous_consumption": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d5_hook_addiction": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d6_nudge_designability": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d7_maslow_level": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}},
  "d8_tech_wave": {{"score": N, "reasoning": "...", "hypothesis": "...", "analogy": "..."}}
}}"""


def score_cluster(cluster_id: int, cycle_id: int) -> dict:
    """Score a single cluster using Opus."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Get cluster samples
    records = sb.table("pain_points").select("*").eq(
        "cycle_id", cycle_id
    ).eq("cluster_id", cluster_id).limit(10).execute().data

    if not records:
        return {"error": "No records found"}

    samples = "\n".join([
        f"- [{r.get('source', '')}] {r.get('title', '')}: {(r.get('content', '') or '')[:200]}"
        for r in records[:5]
    ])

    prompt = SCORING_PROMPT.format(
        cluster_label=records[0].get("cluster_label", f"cluster-{cluster_id}"),
        samples=samples,
    )

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text
    start = text.find("{")
    end = text.rfind("}") + 1
    scores = json.loads(text[start:end])

    # Calculate totals
    outer = (scores["frequency"]["score"] + scores["emotion"]["score"] +
             scores["payment"]["score"] + scores["feasibility"]["score"])
    inner = sum(scores[f"d{i}_{k}"]["score"] for i, k in [
        (1, "social_contagion"), (2, "weak_ties"), (3, "identity_performance"),
        (4, "conspicuous_consumption"), (5, "hook_addiction"),
        (6, "nudge_designability"), (7, "maslow_level"), (8, "tech_wave"),
    ])
    total = outer + inner

    # Star rating
    if total >= 80: star = 5
    elif total >= 65: star = 4
    elif total >= 50: star = 3
    else: star = 0

    # Update all records in cluster
    update_data = {
        "score_frequency": scores["frequency"]["score"],
        "score_emotion": scores["emotion"]["score"],
        "score_payment": scores["payment"]["score"],
        "score_feasibility": scores["feasibility"]["score"],
        "d1_social_contagion": scores["d1_social_contagion"]["score"],
        "d1_reasoning": scores["d1_social_contagion"]["reasoning"],
        "d1_hypothesis": scores["d1_social_contagion"]["hypothesis"],
        "d1_analogy": scores["d1_social_contagion"]["analogy"],
        "d2_weak_ties": scores["d2_weak_ties"]["score"],
        "d2_reasoning": scores["d2_weak_ties"]["reasoning"],
        "d2_hypothesis": scores["d2_weak_ties"]["hypothesis"],
        "d2_analogy": scores["d2_weak_ties"]["analogy"],
        "d3_identity_performance": scores["d3_identity_performance"]["score"],
        "d3_reasoning": scores["d3_identity_performance"]["reasoning"],
        "d3_hypothesis": scores["d3_identity_performance"]["hypothesis"],
        "d3_analogy": scores["d3_identity_performance"]["analogy"],
        "d4_conspicuous_consumption": scores["d4_conspicuous_consumption"]["score"],
        "d4_reasoning": scores["d4_conspicuous_consumption"]["reasoning"],
        "d4_hypothesis": scores["d4_conspicuous_consumption"]["hypothesis"],
        "d4_analogy": scores["d4_conspicuous_consumption"]["analogy"],
        "d5_hook_addiction": scores["d5_hook_addiction"]["score"],
        "d5_reasoning": scores["d5_hook_addiction"]["reasoning"],
        "d5_hypothesis": scores["d5_hook_addiction"]["hypothesis"],
        "d5_analogy": scores["d5_hook_addiction"]["analogy"],
        "d6_nudge_designability": scores["d6_nudge_designability"]["score"],
        "d6_reasoning": scores["d6_nudge_designability"]["reasoning"],
        "d6_hypothesis": scores["d6_nudge_designability"]["hypothesis"],
        "d6_analogy": scores["d6_nudge_designability"]["analogy"],
        "d7_maslow_level": scores["d7_maslow_level"]["score"],
        "d7_reasoning": scores["d7_maslow_level"]["reasoning"],
        "d7_hypothesis": scores["d7_maslow_level"]["hypothesis"],
        "d7_analogy": scores["d7_maslow_level"]["analogy"],
        "d8_tech_wave": scores["d8_tech_wave"]["score"],
        "d8_reasoning": scores["d8_tech_wave"]["reasoning"],
        "d8_hypothesis": scores["d8_tech_wave"]["hypothesis"],
        "d8_analogy": scores["d8_tech_wave"]["analogy"],
        "star_rating": star,
        "processed": True,
        "scored_at": "now()",
    }

    for r in records:
        sb.table("pain_points").update(update_data).eq("id", r["id"]).execute()

    return {"cluster_id": cluster_id, "outer": outer, "inner": inner, "total": total, "star": star}


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cluster_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(f"Scoring cluster {cluster_id} in cycle {cycle_id}...")
    result = score_cluster(cluster_id, cycle_id)
    print(json.dumps(result, indent=2))
