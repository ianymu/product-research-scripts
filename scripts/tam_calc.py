"""
V7 Pipeline — TAM/SAM/SOM Calculator
Uses Perplexity for market research, Claude for analysis.
"""
import os
import json
import sys
import requests
import anthropic
from supabase import create_client

PERPLEXITY_API_KEY = os.environ["PERPLEXITY_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def research_market(direction_name: str) -> str:
    """Research market size via Perplexity."""
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "user", "content": f"What is the Total Addressable Market (TAM), Serviceable Addressable Market (SAM), and Serviceable Obtainable Market (SOM) for {direction_name}? Include market size in USD, growth rate, key trends, recent funding events, and source URLs."},
        ],
    }
    resp = requests.post("https://api.perplexity.ai/chat/completions",
                         headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def calculate_tam(cycle_id: int, direction_id: str, direction_name: str) -> dict:
    """Full TAM/SAM/SOM calculation with trend analysis."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Research
    research = research_market(direction_name)

    # Analyze with Claude
    prompt = f"""Based on this market research, extract structured TAM/SAM/SOM data:

{research}

Respond in JSON:
{{
  "tam": {{"value": NUMBER_IN_USD, "source": "URL", "reasoning": "..."}},
  "sam": {{"value": NUMBER_IN_USD, "source": "URL", "reasoning": "..."}},
  "som": {{"value": NUMBER_IN_USD, "source": "URL", "reasoning": "..."}},
  "trend": "accelerating|stable|decelerating",
  "trend_data": {{"yoy_growth": "X%", "key_events": ["..."]}},
  "confidence": "high|medium|low"
}}"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text
    start = text.find("{")
    end = text.rfind("}") + 1
    result = json.loads(text[start:end])

    # Write to Supabase
    sb.table("market_validations").upsert({
        "cycle_id": cycle_id,
        "direction_id": direction_id,
        "direction_name": direction_name,
        "tam_value": result["tam"]["value"],
        "tam_source": result["tam"]["source"],
        "tam_reasoning": result["tam"]["reasoning"],
        "sam_value": result["sam"]["value"],
        "sam_source": result["sam"]["source"],
        "som_value": result["som"]["value"],
        "som_source": result["som"]["source"],
        "som_reasoning": result["som"]["reasoning"],
        "trend": result["trend"],
        "trend_data": json.dumps(result.get("trend_data", {})),
    }, on_conflict="cycle_id,direction_id").execute()

    return result


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    direction_id = sys.argv[2] if len(sys.argv) > 2 else "dir-001"
    direction_name = sys.argv[3] if len(sys.argv) > 3 else "AI Code Review Tool"
    print(f"Calculating TAM for {direction_name}...")
    result = calculate_tam(cycle_id, direction_id, direction_name)
    print(json.dumps(result, indent=2))
