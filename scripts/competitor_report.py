"""
V7 Pipeline — Competitor Report Generator
SWOT, Rogers adoption, funding, Thiel comparison.
"""
import os
import json
import sys
import requests
import anthropic
from supabase import create_client

PERPLEXITY_API_KEY = os.environ["PERPLEXITY_API_KEY"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()


def analyze_competitor(cycle_id: int, direction_id: str, competitor_name: str) -> dict:
    """Deep competitor analysis."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Research via Perplexity
    headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
    research_resp = requests.post("https://api.perplexity.ai/chat/completions", headers=headers, json={
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": f"Deep analysis of {competitor_name}: product features, pricing, funding history, team size, technology stack, market position, strengths, weaknesses. Include source URLs."}],
    }, timeout=120)
    research = research_resp.json()["choices"][0]["message"]["content"]

    # Structured analysis with Claude
    prompt = f"""Analyze this competitor based on research data:

{research}

Output JSON:
{{
  "competitor_name": "{competitor_name}",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "opportunities": ["..."],
  "threats": ["..."],
  "adoption_stage": "innovators|early_adopters|early_majority|late_majority|laggards",
  "funding_history": [{{"round": "...", "amount": N, "date": "...", "investors": ["..."]}}],
  "total_funding": N,
  "thiel_comparison": {{
    "proprietary_tech": {{"has": true/false, "reasoning": "..."}},
    "network_effects": {{"has": true/false, "reasoning": "..."}},
    "economies_of_scale": {{"has": true/false, "reasoning": "..."}},
    "brand": {{"has": true/false, "reasoning": "..."}}
  }},
  "differentiation_angle": "...",
  "differentiation_score": N
}}"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text
    start = text.find("{")
    end = text.rfind("}") + 1
    result = json.loads(text[start:end])

    # Write to Supabase
    sb.table("competitor_analyses").insert({
        "cycle_id": cycle_id,
        "direction_id": direction_id,
        "competitor_name": competitor_name,
        "competitor_url": "",
        "strengths": json.dumps(result.get("strengths", [])),
        "weaknesses": json.dumps(result.get("weaknesses", [])),
        "opportunities": json.dumps(result.get("opportunities", [])),
        "threats": json.dumps(result.get("threats", [])),
        "adoption_stage": result.get("adoption_stage", "early_adopters"),
        "funding_history": json.dumps(result.get("funding_history", [])),
        "total_funding": result.get("total_funding", 0),
        "thiel_comparison": json.dumps(result.get("thiel_comparison", {})),
        "differentiation_angle": result.get("differentiation_angle", ""),
        "differentiation_score": result.get("differentiation_score", 5),
        "raw_data": json.dumps(result),
    }).execute()

    return result


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    direction_id = sys.argv[2] if len(sys.argv) > 2 else "dir-001"
    competitor = sys.argv[3] if len(sys.argv) > 3 else "Linear"
    print(f"Analyzing competitor: {competitor}...")
    result = analyze_competitor(cycle_id, direction_id, competitor)
    print(json.dumps(result, indent=2))
