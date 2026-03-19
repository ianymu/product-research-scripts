#!/usr/bin/env python3
"""
ResearchShrimp — iPhone Landing Page Generator
When research recommends a direction, user replies OK → generate LP preview.
iPhone 17 Pro Max: 430x932, dark theme, Apple-style design.
Saves to ~/shrimp-web/public/preview/
"""
import os
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("research-lp")

TG_TOKEN = os.environ["TG_SHRIMPILOT_TOKEN"].strip()
CHAT_ID = os.environ["TG_SHRIMPILOT_CHAT_ID"].strip()
WEB_DIR = os.path.expanduser("~/shrimp-web/public/preview")
WEB_HOST = os.environ.get("SHRIMP_WEB_HOST", "http://18.221.160.170:3100").strip()


def tg_send(text: str, chat_id: str = "") -> bool:
    cid = chat_id or CHAT_ID
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id": cid,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=15)
        if resp.status_code != 200:
            httpx.post(url, json={"chat_id": cid, "text": text}, timeout=15)
        return True
    except Exception as e:
        log.warning("TG send error: %s", e)
        return False


def generate_lp(direction: str, score: float, features: list[str],
                value_prop: str = "", chat_id: str = "") -> str:
    """
    Generate iPhone-optimized dark LP and return preview URL.
    """
    slug = hashlib.md5(direction.encode()).hexdigest()[:8]
    filename = f"lp-{slug}.html"
    filepath = os.path.join(WEB_DIR, filename)

    if not features:
        features = ["AI-driven automation", "Real-time insights", "One-person company toolkit"]

    if not value_prop:
        value_prop = f"Solving {direction} for solopreneurs"

    features_html = ""
    icons = ["🎯", "⚡", "🔮", "💡", "🚀", "🛡️"]
    for i, feat in enumerate(features[:6]):
        icon = icons[i % len(icons)]
        features_html += f"""
        <div class="feature-card">
            <div class="feature-icon">{icon}</div>
            <div class="feature-text">{feat}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=430, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{direction} — V7 Validation</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Helvetica Neue', sans-serif;
    background: #000;
    color: #f5f5f7;
    width: 430px;
    min-height: 932px;
    margin: 0 auto;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
  }}
  .hero {{
    padding: 80px 32px 40px;
    text-align: center;
    background: linear-gradient(180deg, #1a1a2e 0%, #000 100%);
  }}
  .badge {{
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    background: rgba(88, 86, 214, 0.2);
    color: #8b8aff;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 24px;
  }}
  .score {{
    font-size: 64px;
    font-weight: 700;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1;
    margin-bottom: 8px;
  }}
  .score-label {{
    font-size: 14px;
    color: #86868b;
    margin-bottom: 32px;
  }}
  h1 {{
    font-size: 32px;
    font-weight: 700;
    line-height: 1.2;
    margin-bottom: 16px;
    letter-spacing: -0.5px;
  }}
  .subtitle {{
    font-size: 17px;
    color: #86868b;
    line-height: 1.5;
    margin-bottom: 40px;
  }}
  .features {{
    padding: 0 24px 40px;
  }}
  .features-title {{
    font-size: 22px;
    font-weight: 600;
    text-align: center;
    margin-bottom: 24px;
  }}
  .feature-card {{
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 16px 20px;
    background: rgba(255,255,255,0.05);
    border-radius: 16px;
    margin-bottom: 12px;
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.08);
  }}
  .feature-icon {{
    font-size: 28px;
    flex-shrink: 0;
  }}
  .feature-text {{
    font-size: 15px;
    color: #d1d1d6;
    line-height: 1.4;
  }}
  .cta-section {{
    padding: 40px 32px 60px;
    text-align: center;
  }}
  .cta-btn {{
    display: block;
    width: 100%;
    padding: 18px;
    border: none;
    border-radius: 14px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    font-size: 17px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.3px;
    margin-bottom: 16px;
  }}
  .cta-note {{
    font-size: 13px;
    color: #86868b;
  }}
  .footer {{
    padding: 20px 32px 40px;
    text-align: center;
    font-size: 12px;
    color: #48484a;
  }}
  .social-proof {{
    padding: 20px 32px;
    text-align: center;
  }}
  .social-proof-text {{
    font-size: 15px;
    color: #86868b;
    font-style: italic;
    line-height: 1.5;
  }}
  .stats {{
    display: flex;
    justify-content: center;
    gap: 32px;
    padding: 24px 32px;
  }}
  .stat {{
    text-align: center;
  }}
  .stat-num {{
    font-size: 28px;
    font-weight: 700;
    color: #8b8aff;
  }}
  .stat-label {{
    font-size: 12px;
    color: #86868b;
    margin-top: 4px;
  }}
</style>
</head>
<body>
<div class="hero">
    <div class="badge">V7 Pipeline Validated</div>
    <div class="score">{score:.0f}</div>
    <div class="score-label">Pain Score (out of 100)</div>
    <h1>{direction}</h1>
    <p class="subtitle">{value_prop}</p>
</div>

<div class="stats">
    <div class="stat">
        <div class="stat-num">23K+</div>
        <div class="stat-label">Pain Points Analyzed</div>
    </div>
    <div class="stat">
        <div class="stat-num">4</div>
        <div class="stat-label">Data Sources</div>
    </div>
    <div class="stat">
        <div class="stat-num">D1-D8</div>
        <div class="stat-label">Academic Scoring</div>
    </div>
</div>

<div class="features">
    <div class="features-title">What We're Building</div>
    {features_html}
</div>

<div class="social-proof">
    <p class="social-proof-text">"Building alone is hard. Building together changes everything."<br>— Based on {score:.0f}-score validated pain point analysis</p>
</div>

<div class="cta-section">
    <button class="cta-btn" onclick="alert('Coming soon!')">Get Early Access</button>
    <p class="cta-note">Join the waitlist. No spam, ever.</p>
</div>

<div class="footer">
    V7 Pipeline — War Room 9-Agent System<br>
    Generated {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} CST
</div>
</body>
</html>"""

    os.makedirs(WEB_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(html)

    preview_url = f"{WEB_HOST}/preview/{filename}"
    log.info("LP generated: %s", filepath)
    return preview_url


def main():
    """CLI test: generate sample LP from top cluster."""
    import research_morning_brief
    # Get top cluster from Supabase
    top5 = research_morning_brief.fetch_pain_top5()
    if top5:
        t = top5[0]
        url = generate_lp(
            direction=t["name"],
            score=t["score"],
            features=[
                "AI-powered pain point discovery",
                "Real-time trend monitoring",
                "Automated market validation",
                "One-click MVP generation",
            ],
            value_prop=f"Top validated direction — score {t['score']}/100",
        )
        print(f"Preview URL: {url}")
        tg_send(f"🔬 *产研推荐* — Landing Page 预览\n\n方向: *{t['name']}*\n分数: {t['score']}/100\n\n📱 [iPhone 预览]({url})")
    else:
        print("No pain points data available")


if __name__ == "__main__":
    main()
