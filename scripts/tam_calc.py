"""
V7 Pipeline — TAM/SAM/SOM Calculator
Uses Perplexity for market research, Claude for analysis.
"""
import os
import json
import sys
import requests
from openai import OpenAI
from supabase import create_client

PERPLEXITY_API_KEY = os.environ["PERPLEXITY_API_KEY"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()


def research_market(direction_name: str, english_query: str = "") -> str:
    """Research market size via Perplexity (English queries per protocol)."""
    # Use English query for search (per CLAUDE.md: search queries in English)
    search_term = english_query or direction_name
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "user", "content": (
                f"What is the Total Addressable Market (TAM), Serviceable Addressable Market (SAM), "
                f"and Serviceable Obtainable Market (SOM) for: {search_term}? "
                f"Include specific market size numbers in USD, compound annual growth rate (CAGR), "
                f"key trends in 2025-2026, recent funding events in this space, and source URLs. "
                f"Consider adjacent markets: online community platforms, creator economy, "
                f"accountability/productivity apps, and solopreneur tools/SaaS."
            )},
        ],
    }
    resp = requests.post("https://api.perplexity.ai/chat/completions",
                         headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def calculate_tam(cycle_id: int, direction_id: str, direction_name: str,
                   english_query: str = "") -> dict:
    """Full TAM/SAM/SOM calculation with trend analysis."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

    # Research (English query for better results)
    if not english_query:
        english_query = "solopreneur community platform with accountability partners, growth tactics sharing, build-in-public features, AI co-pilot, and founder matching"
    research = research_market(direction_name, english_query)

    # Analyze with Claude
    prompt = f"""Based on this market research, extract structured TAM/SAM/SOM data.

IMPORTANT: All values MUST be numeric (in USD). Do NOT return null or "N/A" for values.
If exact data is not available, estimate based on adjacent market data and explain your reasoning.

Research data:
{research}

Respond in JSON:
{{
  "tam": {{"value": NUMBER_IN_USD, "source": "URL or description", "reasoning": "..."}},
  "sam": {{"value": NUMBER_IN_USD, "source": "URL or description", "reasoning": "..."}},
  "som": {{"value": NUMBER_IN_USD, "source": "URL or description", "reasoning": "..."}},
  "trend": "accelerating|stable|decelerating",
  "trend_data": {{"yoy_growth": "X%", "key_events": ["..."]}},
  "confidence": "high|medium|low"
}}"""

    resp = client.chat.completions.create(
        model="gpt-5.4",
        max_completion_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.choices[0].message.content
    start = text.find("{")
    end = text.rfind("}") + 1
    result = json.loads(text[start:end])

    # Write to Supabase (check if row exists first, handle None values)
    tam = result.get("tam", {})
    sam = result.get("sam", {})
    som = result.get("som", {})
    row_data = {
        "cycle_id": cycle_id,
        "direction_id": direction_id,
        "direction_name": direction_name,
        "tam_value": tam.get("value"),
        "tam_source": tam.get("source", ""),
        "tam_reasoning": tam.get("reasoning", ""),
        "sam_value": sam.get("value"),
        "sam_source": sam.get("source", ""),
        "som_value": som.get("value"),
        "som_source": som.get("source", ""),
        "som_reasoning": som.get("reasoning", ""),
        "trend": result.get("trend"),
        "trend_data": json.dumps(result.get("trend_data", {})),
    }

    existing = sb.table("market_validations").select("id").eq(
        "cycle_id", cycle_id
    ).eq("direction_id", direction_id).limit(1).execute()

    if existing.data:
        sb.table("market_validations").update(row_data).eq(
            "id", existing.data[0]["id"]
        ).execute()
    else:
        sb.table("market_validations").insert(row_data).execute()

    return result


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    direction_id = sys.argv[2] if len(sys.argv) > 2 else "dir-001"
    direction_name = sys.argv[3] if len(sys.argv) > 3 else "AI Code Review Tool"
    print(f"Calculating TAM for {direction_name}...")
    result = calculate_tam(cycle_id, direction_id, direction_name)
    print(json.dumps(result, indent=2))
