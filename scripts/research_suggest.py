#!/usr/bin/env python3
from __future__ import annotations
"""
ResearchShrimp 产研建议 + iPhone Landing Page 生成

功能：
  1. 从 Supabase 查询 Top 痛点方向 + 最大涨幅方向
  2. TG 推送建议消息，提示「回复 OK 生成预览网站」
  3. 用户回复 OK → 生成 iPhone 17 Pro Max (430x932) Landing Page
  4. 保存到 ~/shrimp-web/public/preview/ → 返回预览链接
"""
import os
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("research-suggest")

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
TG_TOKEN = os.environ["TG_SHRIMPILOT_TOKEN"].strip()
CHAT_ID = os.environ["TG_SHRIMPILOT_CHAT_ID"].strip()
EC2_IP = "18.221.160.170"
WEB_PORT = 3080

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

PREVIEW_DIR = Path.home() / "shrimp-web" / "public" / "preview"
STATE_FILE = Path.home() / ".shrimpilot" / "memory" / "suggest_state.json"

# ── Supabase Queries ────────────────────────────────────────────────────────

def fetch_top_direction() -> dict | None:
    """Fetch the top pain point direction with trend info."""
    now = datetime.now(timezone.utc)
    d7 = (now - timedelta(days=7)).isoformat()
    d14 = (now - timedelta(days=14)).isoformat()

    # This week
    r1 = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={
            "select": "cluster_label,total_score,outer_score,inner_score",
            "total_score": "not.is.null",
            "collected_at": f"gte.{d7}",
            "limit": "5000",
        },
        headers=SB_HEADERS, timeout=30,
    )
    this_week = r1.json() if r1.status_code == 200 else []

    # Last week
    r2 = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pain_points",
        params={
            "select": "cluster_label,total_score",
            "total_score": "not.is.null",
            "and": f"(collected_at.gte.{d14},collected_at.lt.{d7})",
            "limit": "5000",
        },
        headers=SB_HEADERS, timeout=30,
    )
    last_week = r2.json() if r2.status_code == 200 else []

    # Aggregate this week
    tw = defaultdict(list)
    tw_detail = defaultdict(lambda: {"outer": [], "inner": []})
    for p in this_week:
        lbl = p.get("cluster_label") or "Unknown"
        sc = p.get("total_score")
        if sc is not None:
            tw[lbl].append(sc)
            tw_detail[lbl]["outer"].append(p.get("outer_score", 0) or 0)
            tw_detail[lbl]["inner"].append(p.get("inner_score", 0) or 0)

    tw_avg = {k: round(sum(v)/len(v), 1) for k, v in tw.items() if v}

    # Aggregate last week
    lw = defaultdict(list)
    for p in last_week:
        lbl = p.get("cluster_label") or "Unknown"
        sc = p.get("total_score")
        if sc is not None:
            lw[lbl].append(sc)
    lw_avg = {k: round(sum(v)/len(v), 1) for k, v in lw.items() if v}

    if not tw_avg:
        return None

    # Find top direction and biggest riser
    sorted_dirs = sorted(tw_avg.items(), key=lambda x: x[1], reverse=True)
    top_name, top_score = sorted_dirs[0]

    # Calculate deltas
    max_delta = -999
    max_delta_name = ""
    for name, score in tw_avg.items():
        prev = lw_avg.get(name, score)
        delta = round(score - prev, 1)
        if delta > max_delta:
            max_delta = delta
            max_delta_name = name

    # Decide which to recommend: biggest riser if significant, else top scorer
    if max_delta > 2 and max_delta_name != top_name:
        rec_name = max_delta_name
        rec_score = tw_avg[rec_name]
        rec_reason = f"本周上升最快 (+{max_delta}分)"
    else:
        rec_name = top_name
        rec_score = top_score
        delta = round(rec_score - lw_avg.get(rec_name, rec_score), 1)
        rec_reason = f"持续领先" if delta <= 0 else f"稳步上升 (+{delta}分)"

    det = tw_detail.get(rec_name, {"outer": [0], "inner": [0]})
    avg_outer = round(sum(det["outer"]) / max(len(det["outer"]), 1), 1)
    avg_inner = round(sum(det["inner"]) / max(len(det["inner"]), 1), 1)

    return {
        "name": rec_name,
        "score": rec_score,
        "outer": avg_outer,
        "inner": avg_inner,
        "reason": rec_reason,
        "count": len(tw.get(rec_name, [])),
        "top5": [(n, s) for n, s in sorted_dirs[:5]],
    }


# ── TG Helper ───────────────────────────────────────────────────────────────

def tg_send(text: str, chat_id: str = "") -> bool:
    cid = chat_id or CHAT_ID
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            resp = httpx.post(url, json={
                "chat_id": cid,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=15)
            if resp.status_code != 200:
                httpx.post(url, json={"chat_id": cid, "text": chunk}, timeout=15)
        except Exception as e:
            log.warning("TG send error: %s", e)
            return False
    return True


# ── State Management ────────────────────────────────────────────────────────

def save_state(direction: dict):
    """Save current suggestion state for OK response."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "direction": direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def load_state() -> dict | None:
    """Load last suggestion state."""
    if not STATE_FILE.exists():
        return None
    try:
        state = json.loads(STATE_FILE.read_text())
        # Check if still fresh (within 24h)
        ts = datetime.fromisoformat(state["timestamp"])
        if datetime.now(timezone.utc) - ts > timedelta(hours=24):
            return None
        return state["direction"]
    except Exception:
        return None


# ── iPhone LP Generator ────────────────────────────────────────────────────

def generate_iphone_lp(direction: dict) -> str:
    """Generate iPhone 17 Pro Max (430x932) Landing Page HTML."""
    name = direction["name"]
    score = direction["score"]
    reason = direction["reason"]

    # Generate unique filename
    slug = hashlib.md5(name.encode()).hexdigest()[:8]
    filename = f"suggest-{slug}.html"
    filepath = PREVIEW_DIR / filename

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=430, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{name} — ShrimPilot Research</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0a0a0a;
    color: #f5f5f7;
    width: 430px;
    min-height: 932px;
    margin: 0 auto;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
  }}

  /* Status bar spacer */
  .status-bar {{ height: 54px; }}

  /* Hero section */
  .hero {{
    padding: 20px 28px 40px;
    text-align: center;
  }}
  .badge {{
    display: inline-block;
    background: linear-gradient(135deg, #ff6b35, #f7c948);
    color: #000;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 12px;
    border-radius: 20px;
    margin-bottom: 20px;
    letter-spacing: 0.5px;
  }}
  .hero h1 {{
    font-size: 32px;
    font-weight: 700;
    line-height: 1.2;
    margin-bottom: 12px;
    background: linear-gradient(to right, #fff, #a0a0a0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .hero p {{
    font-size: 17px;
    color: #8e8e93;
    line-height: 1.5;
    max-width: 360px;
    margin: 0 auto;
  }}

  /* Score card */
  .score-card {{
    margin: 0 28px 32px;
    background: linear-gradient(145deg, #1c1c1e, #2c2c2e);
    border-radius: 20px;
    padding: 24px;
    border: 1px solid rgba(255,255,255,0.06);
  }}
  .score-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }}
  .score-label {{
    font-size: 14px;
    color: #8e8e93;
    font-weight: 500;
  }}
  .score-value {{
    font-size: 48px;
    font-weight: 700;
    background: linear-gradient(135deg, #ff6b35, #f7c948);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .score-bar {{
    height: 6px;
    background: #333;
    border-radius: 3px;
    margin-bottom: 16px;
    overflow: hidden;
  }}
  .score-fill {{
    height: 100%;
    background: linear-gradient(to right, #ff6b35, #f7c948);
    border-radius: 3px;
    width: {min(score, 100)}%;
  }}
  .score-detail {{
    display: flex;
    justify-content: space-around;
  }}
  .score-item {{
    text-align: center;
  }}
  .score-item .num {{
    font-size: 20px;
    font-weight: 600;
    color: #fff;
  }}
  .score-item .lbl {{
    font-size: 12px;
    color: #8e8e93;
    margin-top: 4px;
  }}

  /* Pain points */
  .section {{
    margin: 0 28px 32px;
  }}
  .section h2 {{
    font-size: 20px;
    font-weight: 600;
    margin-bottom: 16px;
  }}
  .pain-item {{
    display: flex;
    align-items: center;
    padding: 14px 16px;
    background: #1c1c1e;
    border-radius: 14px;
    margin-bottom: 10px;
    border: 1px solid rgba(255,255,255,0.04);
  }}
  .pain-icon {{
    font-size: 24px;
    margin-right: 14px;
  }}
  .pain-text {{
    flex: 1;
  }}
  .pain-text h3 {{
    font-size: 15px;
    font-weight: 500;
    margin-bottom: 4px;
  }}
  .pain-text p {{
    font-size: 13px;
    color: #8e8e93;
  }}

  /* CTA */
  .cta-section {{
    padding: 20px 28px 40px;
    text-align: center;
  }}
  .cta-btn {{
    display: block;
    width: 100%;
    padding: 18px;
    background: linear-gradient(135deg, #ff6b35, #f7c948);
    color: #000;
    font-size: 17px;
    font-weight: 600;
    border: none;
    border-radius: 14px;
    cursor: pointer;
    letter-spacing: 0.3px;
  }}
  .cta-sub {{
    font-size: 13px;
    color: #8e8e93;
    margin-top: 12px;
  }}

  /* Footer */
  .footer {{
    text-align: center;
    padding: 20px 28px 44px;
    color: #48484a;
    font-size: 12px;
  }}
  .footer a {{
    color: #ff6b35;
    text-decoration: none;
  }}

  /* Trend badge */
  .trend {{
    display: inline-block;
    font-size: 12px;
    font-weight: 500;
    padding: 3px 8px;
    border-radius: 6px;
    background: rgba(52, 199, 89, 0.15);
    color: #34c759;
  }}
</style>
</head>
<body>
  <div class="status-bar"></div>

  <div class="hero">
    <div class="badge">ShrimPilot Research</div>
    <h1>{name}</h1>
    <p>{reason} — AI 产研虾从全网痛点中发现的高潜力方向</p>
  </div>

  <div class="score-card">
    <div class="score-header">
      <div>
        <div class="score-label">痛点综合评分</div>
        <div class="trend">📈 {reason}</div>
      </div>
      <div class="score-value">{score}</div>
    </div>
    <div class="score-bar"><div class="score-fill"></div></div>
    <div class="score-detail">
      <div class="score-item">
        <div class="num">{direction['outer']}</div>
        <div class="lbl">外层/40</div>
      </div>
      <div class="score-item">
        <div class="num">{direction['inner']}</div>
        <div class="lbl">内层/60</div>
      </div>
      <div class="score-item">
        <div class="num">{direction['count']}</div>
        <div class="lbl">数据量</div>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>为什么值得关注</h2>
    <div class="pain-item">
      <div class="pain-icon">🔥</div>
      <div class="pain-text">
        <h3>高频痛点</h3>
        <p>多个社区反复提及，需求真实且迫切</p>
      </div>
    </div>
    <div class="pain-item">
      <div class="pain-icon">💰</div>
      <div class="pain-text">
        <h3>付费意愿强</h3>
        <p>用户愿意为解决方案付费，市场空间大</p>
      </div>
    </div>
    <div class="pain-item">
      <div class="pain-icon">⚡</div>
      <div class="pain-text">
        <h3>72h 可验证</h3>
        <p>技术可行性高，MVP 可快速搭建</p>
      </div>
    </div>
    <div class="pain-item">
      <div class="pain-icon">📈</div>
      <div class="pain-text">
        <h3>趋势上升</h3>
        <p>本周热度{reason}，窗口期明确</p>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>评分维度 (D1-D8)</h2>
    <div class="pain-item">
      <div class="pain-icon">🌊</div>
      <div class="pain-text">
        <h3>D1 社会传染力 + D2 弱联系扩散</h3>
        <p>产品天然具有口碑传播属性</p>
      </div>
    </div>
    <div class="pain-item">
      <div class="pain-icon">🎭</div>
      <div class="pain-text">
        <h3>D3 身份表演性 + D4 炫耀性消费</h3>
        <p>用户愿意展示使用体验</p>
      </div>
    </div>
    <div class="pain-item">
      <div class="pain-icon">🪝</div>
      <div class="pain-text">
        <h3>D5 Hook 成瘾性 + D6 Nudge</h3>
        <p>高留存潜力，行为设计空间大</p>
      </div>
    </div>
  </div>

  <div class="cta-section">
    <button class="cta-btn" onclick="alert('发送 BUILD 启动 MVP 构建')">
      启动 MVP 构建 →
    </button>
    <div class="cta-sub">V7 Pipeline Stage 5 — 72h 快速验证</div>
  </div>

  <div class="footer">
    Powered by <a href="#">ShrimPilot</a> × V7 Pipeline<br>
    产研虾 ResearchShrimp — {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} CST
  </div>
</body>
</html>"""

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(html, encoding="utf-8")
    log.info("LP saved: %s", filepath)

    url = f"http://{EC2_IP}:{WEB_PORT}/preview/{filename}"
    return url


# ── Public API (called by router) ──────────────────────────────────────────

def run_suggest(chat_id: str = "") -> str:
    """Generate suggestion message. Returns the TG message text."""
    target = chat_id or CHAT_ID
    log.info("Generating research suggestion...")

    direction = fetch_top_direction()
    if not direction:
        msg = "🔬 *产研建议*\n\n暂无足够数据生成建议，请等待下一轮采集完成。"
        tg_send(msg, target)
        return msg

    # Save state for OK response
    save_state(direction)

    # Build suggestion message
    top5_lines = []
    for i, (n, s) in enumerate(direction["top5"], 1):
        marker = " ← 推荐" if n == direction["name"] else ""
        top5_lines.append(f"  {i}. {n} ({s}分){marker}")

    msg = (
        f"🔬 *产研建议* — {datetime.now(timezone(timedelta(hours=8))).strftime('%-m月%-d日')}\n\n"
        f"📊 *当前 Top 5 方向*\n"
        + "\n".join(top5_lines) + "\n\n"
        f"💡 *推荐关注: {direction['name']}*\n"
        f"  综合评分: 🔥{direction['score']} (外{direction['outer']}+内{direction['inner']})\n"
        f"  趋势: {direction['reason']}\n"
        f"  数据支撑: {direction['count']} 条痛点\n\n"
        f"🌐 回复 *OK* 生成 iPhone 预览网站"
    )

    tg_send(msg, target)
    return msg


def handle_ok(chat_id: str = "") -> str:
    """Handle OK response — generate LP and return link."""
    target = chat_id or CHAT_ID
    direction = load_state()

    if not direction:
        msg = "⚠️ 没有待处理的建议。请先发送 `产研建议` 获取推荐。"
        tg_send(msg, target)
        return msg

    tg_send("⏳ 正在生成 iPhone 预览网站...", target)

    url = generate_iphone_lp(direction)

    msg = (
        f"✅ *预览网站已生成*\n\n"
        f"📱 方向: {direction['name']}\n"
        f"🔗 预览: {url}\n\n"
        f"_iPhone 17 Pro Max 尺寸 (430×932)_\n"
        f"_发送 `BUILD` 启动 MVP 构建_"
    )
    tg_send(msg, target)
    return msg


def main():
    """CLI: run_suggest or handle_ok."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ok", action="store_true", help="Handle OK response")
    args = parser.parse_args()

    if args.ok:
        handle_ok()
    else:
        run_suggest()


if __name__ == "__main__":
    main()
