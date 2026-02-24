"""
V7 Pipeline — Perplexity Deep Research
English queries only. Returns structured findings with source URLs.
"""
import os
import json
import sys
import requests

PERPLEXITY_API_KEY = os.environ["PERPLEXITY_API_KEY"]
API_URL = "https://api.perplexity.ai/chat/completions"


def search(query: str, mode: str = "deep_research") -> dict:
    """Execute Perplexity deep research query."""
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": "You are a research assistant. Provide structured findings with source URLs. Always respond in English."},
            {"role": "user", "content": query},
        ],
    }

    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    answer = data["choices"][0]["message"]["content"]
    sources = data.get("citations", [])

    return {
        "answer": answer,
        "sources": sources,
        "model": data.get("model", ""),
        "usage": data.get("usage", {}),
    }


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What are the top SaaS pain points in 2026?"
    print(f"Searching: {query}")
    result = search(query)
    print(json.dumps(result, indent=2, ensure_ascii=False))
