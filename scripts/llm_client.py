#!/usr/bin/env python3
"""
llm_client.py — 统一 LLM 调用客户端
支持: OpenAI (ChatGPT 5.4), Gemini 2.5, Moonshot Kimi, Anthropic Claude
所有 provider 走 OpenAI-compatible SDK，Gemini 走 google HTTP API
"""
import os
import json
import logging
import httpx
from openai import OpenAI

log = logging.getLogger("llm_client")

# ─── API Keys ─────────────────────────────────────────────
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MOONSHOT_KEY = os.environ.get("MOONSHOT_API_KEY", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# ─── Clients (lazy init) ──────────────────────────────────
_clients = {}

def _get_openai():
    if "openai" not in _clients:
        _clients["openai"] = OpenAI(api_key=OPENAI_KEY)
    return _clients["openai"]

def _get_moonshot():
    if "moonshot" not in _clients:
        _clients["moonshot"] = OpenAI(
            api_key=MOONSHOT_KEY,
            base_url="https://api.moonshot.ai/v1",
        )
    return _clients["moonshot"]

def _get_anthropic():
    if "anthropic" not in _clients:
        import anthropic
        _clients["anthropic"] = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _clients["anthropic"]


# ─── Model Router ─────────────────────────────────────────
# Model aliases → (provider, actual_model_id)
MODEL_MAP = {
    # ChatGPT 5.4 系列
    "chatgpt-5.4-thinking": ("openai", "gpt-5.4"),  # 5.4 标准版自带 thinking
    "chatgpt-5.4-mini":     ("openai", "gpt-5.4-mini"),
    "chatgpt-5.4":          ("openai", "gpt-5.4"),
    # Gemini 2.5 系列
    "gemini-flash":         ("gemini", "gemini-2.5-flash"),
    "gemini-flash-image":   ("gemini", "gemini-2.5-flash-image"),
    # Kimi
    "kimi":                 ("moonshot", "kimi-k2.5"),
    # Claude (仅代码生成)
    "claude-opus":          ("anthropic", "claude-opus-4-6"),
    "claude-sonnet":        ("anthropic", "claude-sonnet-4-5-20241022"),
}


def call_llm(model: str, system: str, user: str, max_tokens: int = 4096,
             temperature: float = 0.7, response_format: str = None) -> str:
    """
    统一 LLM 调用。返回纯文本。
    model: MODEL_MAP 中的别名，或直接写 provider model ID
    """
    if model in MODEL_MAP:
        provider, model_id = MODEL_MAP[model]
    else:
        # 猜 provider
        if model.startswith("gpt-") or model.startswith("chatgpt-"):
            provider, model_id = "openai", model
        elif model.startswith("gemini-"):
            provider, model_id = "gemini", model
        elif model.startswith("kimi-"):
            provider, model_id = "moonshot", model
        elif model.startswith("claude-"):
            provider, model_id = "anthropic", model
        else:
            raise ValueError(f"Unknown model: {model}")

    log.info(f"LLM call: {provider}/{model_id} (max_tokens={max_tokens})")

    if provider == "openai":
        return _call_openai(model_id, system, user, max_tokens, temperature)
    elif provider == "moonshot":
        return _call_moonshot(model_id, system, user, max_tokens, temperature)
    elif provider == "gemini":
        return _call_gemini(model_id, system, user, max_tokens, temperature)
    elif provider == "anthropic":
        return _call_anthropic(model_id, system, user, max_tokens, temperature)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _call_openai(model_id, system, user, max_tokens, temperature):
    client = _get_openai()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        max_completion_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content


def _call_moonshot(model_id, system, user, max_tokens, temperature):
    client = _get_moonshot()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        max_completion_tokens=max_tokens,
        
    )
    return resp.choices[0].message.content


def _call_gemini(model_id, system, user, max_tokens, temperature):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}"
    body = {
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    with httpx.Client(timeout=120) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_anthropic(model_id, system, user, max_tokens, temperature):
    client = _get_anthropic()
    kwargs = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return resp.content[0].text


def call_llm_vision(model: str, system: str, user_text: str,
                     image_data: str, image_media_type: str = "image/png",
                     max_tokens: int = 4096) -> str:
    """Vision 调用（Gemini 3.1）"""
    if model in MODEL_MAP:
        provider, model_id = MODEL_MAP[model]
    else:
        provider, model_id = "gemini", model

    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}"
        body = {
            "contents": [{
                "parts": [
                    {"text": user_text},
                    {"inline_data": {"mime_type": image_media_type, "data": image_data}},
                ]
            }],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    else:
        raise ValueError(f"Vision not supported for provider: {provider}")


if __name__ == "__main__":
    # Quick test
    import sys
    logging.basicConfig(level=logging.INFO)
    model = sys.argv[1] if len(sys.argv) > 1 else "chatgpt-5.4-mini"
    result = call_llm(model, "You are a helpful assistant.", "Say hello in one sentence.", max_tokens=50)
    print(f"[{model}] → {result}")
