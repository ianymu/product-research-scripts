"""
V7 Pipeline — Capital Assessment (Stage 3)
Scorecard (6 factors × weights) + VC Valuation + Thiel Monopoly Test.
Reads TAM + competitor data from Supabase, produces capital gate verdict.
"""
import os
import json
import sys
from datetime import datetime, timezone
from openai import OpenAI
from supabase import create_client

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()


def load_validation_data(sb, cycle_id: int, direction_id: str) -> dict:
    """Load TAM + competitor data from Supabase for this direction."""
    # TAM data
    tam_resp = sb.table("market_validations").select("*").eq(
        "cycle_id", cycle_id
    ).eq("direction_id", direction_id).execute()
    tam_data = tam_resp.data[0] if tam_resp.data else {}

    # Competitor data
    comp_resp = sb.table("competitor_analyses").select("*").eq(
        "cycle_id", cycle_id
    ).eq("direction_id", direction_id).execute()
    competitors = comp_resp.data or []

    return {"tam": tam_data, "competitors": competitors}


def run_assessment(cycle_id: int, direction_id: str,
                   direction_name: str) -> dict:
    """Run full capital assessment: Scorecard + VC Valuation + Thiel Test."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

    # Load existing data
    data = load_validation_data(sb, cycle_id, direction_id)
    tam_data = data["tam"]
    competitors = data["competitors"]

    # Build context for Claude
    tam_summary = ""
    if tam_data:
        tam_summary = (
            f"TAM: ${tam_data.get('tam_value', 'N/A')} "
            f"(source: {tam_data.get('tam_source', 'N/A')})\n"
            f"SAM: ${tam_data.get('sam_value', 'N/A')}\n"
            f"SOM: ${tam_data.get('som_value', 'N/A')}\n"
            f"Trend: {tam_data.get('trend', 'N/A')}\n"
            f"TAM reasoning: {tam_data.get('tam_reasoning', 'N/A')}"
        )

    comp_summary = ""
    for c in competitors:
        thiel = c.get("thiel_comparison", "{}")
        if isinstance(thiel, str):
            thiel = json.loads(thiel)
        comp_summary += (
            f"\n--- {c['competitor_name']} ---\n"
            f"Strengths: {c.get('strengths', '[]')}\n"
            f"Weaknesses: {c.get('weaknesses', '[]')}\n"
            f"Funding: ${c.get('total_funding', 0)}\n"
            f"Adoption: {c.get('adoption_stage', 'N/A')}\n"
            f"Thiel: {json.dumps(thiel)}\n"
        )

    # Load Stage 2 pain_points summary for this direction
    pain_summary = ""
    try:
        pain_resp = sb.table("pain_points").select(
            "cluster_label,total_score,category"
        ).eq("cycle_id", cycle_id).not_.is_(
            "total_score", "null"
        ).order("total_score", desc=True).limit(50).execute()
        if pain_resp.data:
            seen_clusters = {}
            for p in pain_resp.data:
                label = p.get("cluster_label", "unknown")
                if label not in seen_clusters:
                    seen_clusters[label] = p.get("total_score", 0)
            pain_lines = [f"  - {label}: {score}/100" for label, score in seen_clusters.items()]
            top_score = max(seen_clusters.values()) if seen_clusters else 0
            pain_summary = (
                f"Stage 2 clusters (top score {top_score}/100):\n"
                + "\n".join(pain_lines[:10])
            )
    except Exception:
        pass

    prompt = f"""You are a venture capital analyst using the Bill Payne Scorecard Method.

## Product Direction
"{direction_name}" (direction_id: {direction_id}, cycle: {cycle_id})

## Stage 2 Pain Analysis
{pain_summary if pain_summary else "No Stage 2 data available."}

## Market Data
{tam_summary if tam_summary else "No TAM data available yet."}

## Competitor Landscape
{comp_summary if comp_summary else "No competitor data available yet."}

## Your Task

Produce a JSON assessment with these 3 components:

### 1. Bill Payne Scorecard Method
This is the standard angel/seed-stage scorecard. Rate each factor as a PERCENTAGE where:
- 100% = average startup at this stage (baseline)
- >100% = above average (e.g., 120% = strong advantage)
- <100% = below average (e.g., 70% = notable weakness)
- Typical range: 60% to 140%

Apply these weights:
- Team (30%): Solo founder with AI/automation stack. Calibration: solo technical founders like Pieter Levels (NomadList, $2M+ ARR solo) score ~80%. Solo with proven distribution scores ~90%. Solo with no track record ~60-70%.
- Market (25%): Based on TAM/SAM/SOM data. >$10B TAM with accelerating trend = 110-130%.
- Product (15%): Based on Stage 2 pain score. >=65/100 = validated demand ~100-110%.
- Competition (10%): Fragmented with no dominant player = 100-120%. Red ocean = 60-80%.
- Marketing (10%): Strong organic/viral channels = 100-120%. Unproven = 70-90%.
- Fundraising/Other (5%): Bootstrappable with revenue path = 80-100%.
- Other/Timing (5%): Macro tailwinds (AI wave, remote work trend) = 100-130%.

Calculate weighted_pct = sum of (factor_pct × weight). Example: Team 80% × 30% = 24%.

### 2. VC Valuation Method
- Estimate Year 5 revenue (monthly × 12 × growth)
- Apply PS multiple from comparable exits (community/SaaS)
- Calculate: Exit Value → Pre-Money = Exit ÷ Target Return × (1 - dilution)
- Reference at least 1 comparable exit

### 3. Thiel Monopoly Test (score 0-4)
For the PRODUCT (not competitors), assess:
- Proprietary Technology: 10x better than alternatives?
- Network Effects: Does product get better with more users?
- Economies of Scale: Can it scale with near-zero marginal cost?
- Brand: Can it own a category/identity?

Output ONLY this JSON:
{{
  "scorecard": {{
    "team": {{"score": N, "weight": 0.30, "reasoning": "..."}},
    "market": {{"score": N, "weight": 0.25, "reasoning": "..."}},
    "product": {{"score": N, "weight": 0.15, "reasoning": "..."}},
    "competition": {{"score": N, "weight": 0.10, "reasoning": "..."}},
    "marketing": {{"score": N, "weight": 0.10, "reasoning": "..."}},
    "fundraising_other": {{"score": N, "weight": 0.05, "reasoning": "..."}},
    "other_timing": {{"score": N, "weight": 0.05, "reasoning": "..."}},
    "weighted_pct": N
  }},
  "vc_valuation": {{
    "y5_revenue_estimate": N,
    "ps_multiple": N,
    "comparable_exit": "company name — details",
    "exit_value": N,
    "target_return": N,
    "dilution_rate": N,
    "pre_money_valuation": N,
    "reasoning": "..."
  }},
  "thiel_test": {{
    "proprietary_tech": {{"has": true/false, "reasoning": "..."}},
    "network_effects": {{"has": true/false, "reasoning": "..."}},
    "economies_of_scale": {{"has": true/false, "reasoning": "..."}},
    "brand": {{"has": true/false, "reasoning": "..."}},
    "score": N,
    "verdict": "monopoly_potential|competitive|weak"
  }},
  "overall_verdict": {{
    "investable": true/false,
    "summary": "1-2 sentence verdict",
    "key_risk": "...",
    "key_strength": "..."
  }}
}}

IMPORTANT: "score" in scorecard factors is a PERCENTAGE (e.g., 80, 95, 120), NOT a 1-5 scale. weighted_pct is also a percentage (e.g., 95 means 95%)."""

    resp = client.chat.completions.create(
        model="gpt-5.4",
        max_completion_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.choices[0].message.content
    start = text.find("{")
    end = text.rfind("}") + 1
    result = json.loads(text[start:end])

    # Write to Supabase market_validations (map to actual table columns)
    sc = result.get("scorecard", {})
    vc = result.get("vc_valuation", {})
    thiel = result.get("thiel_test", {})

    # --- Gate logic: PASS / MAYBE / KILL ---
    scorecard_pct = sc.get("weighted_pct", 0) or 0
    thiel_score = thiel.get("score", 0) or 0

    gate_scorecard_pass = scorecard_pct >= 100
    gate_thiel_pass = thiel_score >= 3

    # Determine gate verdict (three-way: PASS / MAYBE / KILL)
    if gate_scorecard_pass and gate_thiel_pass:
        gate_verdict = "PASS"
    elif scorecard_pct >= 80 and not gate_thiel_pass:
        # Close on scorecard but weak moat → MAYBE (human review)
        gate_verdict = "MAYBE"
    elif gate_thiel_pass and scorecard_pct >= 75:
        # Strong moat but scorecard slightly below → MAYBE
        gate_verdict = "MAYBE"
    elif scorecard_pct >= 90:
        # Very close to PASS on scorecard alone → MAYBE
        gate_verdict = "MAYBE"
    else:
        gate_verdict = "KILL"

    print(f"Gate: scorecard={scorecard_pct}%, thiel={thiel_score}/4 → {gate_verdict}")

    update_data = {
        # Scorecard columns (now percentages, e.g. 80 = 80%)
        "scorecard_team": sc.get("team", {}).get("score"),
        "scorecard_market": sc.get("market", {}).get("score"),
        "scorecard_product": sc.get("product", {}).get("score"),
        "scorecard_competition": sc.get("competition", {}).get("score"),
        "scorecard_marketing": sc.get("marketing", {}).get("score"),
        "scorecard_fundraising": sc.get("fundraising_other", {}).get("score"),
        "scorecard_weighted": scorecard_pct,
        # VC valuation columns
        "vc_year5_revenue": vc.get("y5_revenue_estimate"),
        "vc_ps_multiple": vc.get("ps_multiple"),
        "vc_exit_value": vc.get("exit_value"),
        "vc_return_multiple": vc.get("target_return"),
        "vc_dilution_rate": vc.get("dilution_rate"),
        "vc_pre_money": vc.get("pre_money_valuation"),
        # Thiel columns
        "thiel_proprietary_tech": thiel.get("proprietary_tech", {}).get("has", False),
        "thiel_network_effects": thiel.get("network_effects", {}).get("has", False),
        "thiel_economies_of_scale": thiel.get("economies_of_scale", {}).get("has", False),
        "thiel_brand": thiel.get("brand", {}).get("has", False),
        "thiel_score": thiel_score,
        # Gate verdict (PASS / MAYBE / KILL)
        "gate_scorecard_pass": gate_scorecard_pass,
        "gate_thiel_pass": gate_thiel_pass,
        "gate_verdict": gate_verdict,
        # Full reports as text (for detailed reasoning)
        "report_c_capital": json.dumps(result, ensure_ascii=False),
        "report_f_recommendation": json.dumps(result.get("overall_verdict", {}), ensure_ascii=False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if tam_data and tam_data.get("id"):
        sb.table("market_validations").update(update_data).eq(
            "id", tam_data["id"]
        ).execute()
        print(f"Updated market_validations row {tam_data['id']}")
    else:
        update_data.update({
            "cycle_id": cycle_id,
            "direction_id": direction_id,
            "direction_name": direction_name,
        })
        sb.table("market_validations").insert(update_data).execute()
        print("Inserted new market_validations row")

    return result


def format_summary(result: dict) -> str:
    """Format assessment result for human reading / TG push."""
    sc = result.get("scorecard", {})
    thiel = result.get("thiel_test", {})
    vc = result.get("vc_valuation", {})
    verdict = result.get("overall_verdict", {})

    thiel_count = sum(1 for k in ["proprietary_tech", "network_effects",
                                   "economies_of_scale", "brand"]
                      if thiel.get(k, {}).get("has", False))

    scorecard_pct = sc.get("weighted_pct", "N/A")
    # Determine gate verdict from scores
    sc_val = scorecard_pct if isinstance(scorecard_pct, (int, float)) else 0
    if sc_val >= 100 and thiel_count >= 3:
        gate = "PASS"
    elif sc_val >= 80 or (thiel_count >= 3 and sc_val >= 75) or sc_val >= 90:
        gate = "MAYBE"
    else:
        gate = "KILL"

    lines = [
        "=== Capital Assessment ===",
        f"Scorecard: {scorecard_pct}% (gate: >= 100%)",
        f"  Team({sc.get('team', {}).get('score', '?')}%) "
        f"Market({sc.get('market', {}).get('score', '?')}%) "
        f"Product({sc.get('product', {}).get('score', '?')}%) "
        f"Competition({sc.get('competition', {}).get('score', '?')}%) "
        f"Marketing({sc.get('marketing', {}).get('score', '?')}%) "
        f"Fund({sc.get('fundraising_other', {}).get('score', '?')}%) "
        f"Other({sc.get('other_timing', {}).get('score', '?')}%)",
        f"VC Valuation: Pre-Money ${vc.get('pre_money_valuation', 'N/A')}",
        f"  Y5 Rev: ${vc.get('y5_revenue_estimate', 'N/A')} × {vc.get('ps_multiple', 'N/A')}x PS",
        f"  Comparable: {vc.get('comparable_exit', 'N/A')}",
        f"Thiel Test: {thiel_count}/4 (gate: >= 3/4)",
        f"  Tech: {'Y' if thiel.get('proprietary_tech', {}).get('has') else 'N'} | "
        f"Network: {'Y' if thiel.get('network_effects', {}).get('has') else 'N'} | "
        f"Scale: {'Y' if thiel.get('economies_of_scale', {}).get('has') else 'N'} | "
        f"Brand: {'Y' if thiel.get('brand', {}).get('has') else 'N'}",
        f"Gate Verdict: {gate}",
        f"  {verdict.get('summary', '')}",
        f"  Risk: {verdict.get('key_risk', '')}",
        f"  Strength: {verdict.get('key_strength', '')}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2001
    direction_id = sys.argv[2] if len(sys.argv) > 2 else "solopreneur-community"
    direction_name = sys.argv[3] if len(sys.argv) > 3 else "独立创业者社群平台"

    print(f"Running capital assessment for: {direction_name}...")
    result = run_assessment(cycle_id, direction_id, direction_name)
    print(format_summary(result))
    print("\n--- Raw JSON ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))
