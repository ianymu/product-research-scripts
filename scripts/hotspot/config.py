#!/usr/bin/env python3
"""
hotspot/config.py — OpsShrimp v2 配置中心
账号列表 + 环境变量 + Supabase/Perplexity/Claude 共享工具函数

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
if "/home/ec2-user/scripts" not in sys.path:
    sys.path.insert(0, "/home/ec2-user/scripts")
import sys
import json
import re
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from collections import Counter

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# === Logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("hotspot")

# === Environment Variables (铁律 #1: 全部 .strip()) ===
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
APIFY_KEY = os.environ.get("APIFY_API_KEY", "").strip()
TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

# === 对标账号列表 ===

WECHAT_ACCOUNTS = [
    "AGI Hunt", "新智元", "AI信息Gap", "51CTO技术栈", "机器之心", "智东西", "量子位",
]

XHS_ACCOUNTS = [
    "歸藏的AI工具箱", "数字生命卡兹克", "AI产品经理大本营", "花生酱先生",
    "赛博禅心", "万能X女士", "独立女生小",
    "奇域AI", "Patrick杀死朽木", "赵纯想",
]

X_ACCOUNTS = [
    "karpathy", "AndrewYNg", "ylecun", "rowancheung", "AravSrinivas",
    "sama", "levelsio", "marclouvion", "dannypostmaa", "gregisenberg",
    "aisolopreneur", "swyx", "csallen",
]

# === Supabase Helpers ===

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def sb_insert(table: str, rows: list) -> bool:
    if not rows:
        return True
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=SB_HEADERS,
                json=rows,
            )
            if resp.status_code in (200, 201):
                log.info(f"Inserted {len(rows)} rows into {table}")
                return True
            log.error(f"Supabase insert failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log.error(f"Supabase insert error: {e}")
        return False


def sb_upsert(table: str, rows: list, on_conflict: str = "content_hash") -> bool:
    if not rows:
        return True
    try:
        headers = {**SB_HEADERS, "Prefer": "resolution=merge-duplicates"}
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}",
                headers=headers,
                json=rows,
            )
            if resp.status_code in (200, 201):
                log.info(f"Upserted {len(rows)} rows into {table}")
                return True
            log.error(f"Supabase upsert failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log.error(f"Supabase upsert error: {e}")
        return False


def sb_query(path: str) -> list:
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{SUPABASE_URL}/rest/v1/{path}",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                },
            )
            return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        log.error(f"Supabase query error: {e}")
        return []


# === Perplexity Search ===

def perplexity_search(query: str, focus: str = "internet") -> dict:
    if not PERPLEXITY_KEY:
        log.warning("PERPLEXITY_API_KEY not set, skipping search")
        return {"answer": "", "citations": []}
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": "You are a research assistant. Return factual data with sources."},
                        {"role": "user", "content": query},
                    ],
                    "search_recency_filter": "day",
                },
            )
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])
            return {"answer": answer, "citations": citations}
    except Exception as e:
        log.error(f"Perplexity search error: {e}")
        return {"answer": "", "citations": []}


# === Claude Keyword Extraction ===

EXTRACTION_SYSTEM = """You extract trending topics from content platform data.
Output JSON array. Each item:
{
  "source_name": "博主/账号名",
  "title": "文章/笔记标题",
  "content_preview": "前100字内容摘要",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "topic_cluster": "归属话题 (如: AI编程/Agent/一人公司/效率工具/创业融资)",
  "hotspot_score": 0-100 (基于讨论热度、时效性、影响力估算),
  "estimated_post_time": "发布时间估算 (如: 08:30, morning, 未知)",
  "source_url": "原文URL(如有)"
}
Only include real, verifiable content. Do NOT fabricate data."""


def extract_keywords_and_topics(text: str, platform: str) -> list[dict]:
    try:
        from llm_client import call_llm
        result = call_llm(
            "chatgpt-5.4-thinking",
            EXTRACTION_SYSTEM,
            f"Platform: {platform}\n\nRaw data:\n{text[:8000]}",
            max_tokens=3000,
        )
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        log.error(f"ChatGPT extraction error: {e}")
    return _regex_keyword_extract(text, platform)


def _regex_keyword_extract(text: str, platform: str) -> list[dict]:
    words = re.findall(r'[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}', text)
    counter = Counter(words)
    top_keywords = [w for w, _ in counter.most_common(20) if len(w) >= 2]
    return [{
        "source_name": f"{platform}_aggregate",
        "title": f"{platform} 热点聚合",
        "content_preview": text[:200],
        "keywords": top_keywords[:5],
        "topic_cluster": "综合热点",
        "hotspot_score": 50,
        "estimated_post_time": "未知",
        "source_url": "",
    }]


# === Content Hash for Dedup ===

def content_hash(platform: str, source_name: str, title: str) -> str:
    raw = f"{platform}|{source_name}|{title[:100]}"
    return hashlib.sha256(raw.encode()).hexdigest()


# === Date Helpers ===

def yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def week_ago() -> str:
    return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
