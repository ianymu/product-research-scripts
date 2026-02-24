"""
V7 Pipeline — Report Generator
Generates comprehensive pipeline reports at any stage.
"""
import os
import json
import sys
import anthropic
from supabase import create_client

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def generate_report(cycle_id: int, direction_id: str) -> str:
    """Generate full validation report (sections A-F)."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Gather data
    market = sb.table("market_validations").select("*").eq(
        "direction_id", direction_id).single().execute().data
    competitors = sb.table("competitor_analyses").select("*").eq(
        "direction_id", direction_id).execute().data
    pain_points = sb.table("pain_points").select("*").eq(
        "cycle_id", cycle_id).order("total_score", desc=True).limit(5).execute().data

    prompt = f"""Generate a comprehensive V7 validation report with sections A-F.

## Data
Market: {json.dumps(market, default=str)}
Competitors: {json.dumps(competitors, default=str)}
Top Pain Points: {json.dumps(pain_points, default=str)}

## Report Structure
A. Market Size & Trends (TAM/SAM/SOM + trend analysis)
B. Competitive Landscape (competitor matrix + SWOT summary)
C. Capital Favorability (Scorecard + VC Method + Thiel analysis)
D. Validation Data (LP results + pain point evidence)
E. Risk Factors (top 5 risks with mitigation)
F. Recommendation (GO/MAYBE/KILL with reasoning)

Write in English. Be data-driven. Cite sources where available."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    report = resp.content[0].text

    # Save to Supabase
    sb.table("market_validations").update({
        "report_a_market": "See full report",
        "report_f_recommendation": report[:500],
    }).eq("direction_id", direction_id).execute()

    return report


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    direction_id = sys.argv[2] if len(sys.argv) > 2 else "dir-001"
    print(f"Generating report for direction {direction_id}...")
    report = generate_report(cycle_id, direction_id)
    print(report)
