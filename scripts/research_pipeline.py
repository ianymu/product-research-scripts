#!/usr/bin/env python3
"""
Research Pipeline — 产研虾全链路自动编排

完整流程（用户只需在 TG 回复 GO/KILL）：
  1. pain_aggregator → 聚合痛点成应用方向 → TG 推送（含竞对+市场+价值）
  2. 等待用户 TG 回复 GO [编号] 确认
  3. 用 designer Agent 为确认方向生成美观 Demo HTML
  4. 部署 Demo 到 EC2 静态服务 → TG 推送预览 URL
  5. 等待用户反馈（截图/文字）→ feedback_handler 分析 → 修复 → 重新推送
  6. 用户确认 OK → deep_research_writer 生成公众号文章
  7. github_publisher 生成 README + Landing Page
  8. TG 推送所有成果链接

Usage:
  python3 research_pipeline.py                    # 全流程（带 TG 交互）
  python3 research_pipeline.py --step aggregate   # 只跑聚合
  python3 research_pipeline.py --step demo --direction "AI Coding"  # 只跑 Demo 生成
  python3 research_pipeline.py --auto             # 全自动（不等用户确认，直接处理 GO 候选）

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY,
     TG_SHRIMPILOT_TOKEN, TG_SHRIMPILOT_CHAT_ID
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from anthropic import Anthropic

# ── Config ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()

SCRIPTS_DIR = Path(__file__).parent
OUTPUT_DIR = Path.home() / "research-output"
DEMO_DIR = Path.home() / "demo-sites"
DEMO_BASE_URL = os.environ.get("DEMO_BASE_URL", "http://18.221.160.170:8090").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("research-pipeline")

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── TG Helpers ──────────────────────────────────────────────────────────────

def tg_send(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info("[DRY] %s", text[:200])
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            resp = httpx.post(url, json={
                "chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown",
            }, timeout=15)
            if resp.status_code != 200:
                httpx.post(url, json={"chat_id": TG_CHAT_ID, "text": chunk}, timeout=15)
        except Exception as e:
            log.warning("TG error: %s", e)
            return False
    return True


def tg_wait_for_reply(keyword_filter: str = "", timeout_minutes: int = 60) -> str | None:
    """Wait for a TG reply matching optional keyword filter."""
    if not TG_TOKEN:
        log.info("[DRY] Waiting for TG reply (keyword=%s)...", keyword_filter)
        return None

    log.info("Waiting for TG reply (keyword=%s, timeout=%dmin)...", keyword_filter, timeout_minutes)
    start = time.time()
    offset = 0

    while (time.time() - start) < timeout_minutes * 60:
        try:
            resp = httpx.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
            )
            updates = resp.json().get("result", [])
        except Exception:
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            if chat_id == TG_CHAT_ID and text:
                if not keyword_filter or keyword_filter.upper() in text.upper():
                    log.info("Got reply: %s", text[:100])
                    return text

        time.sleep(2)

    log.warning("Timeout waiting for TG reply")
    return None


# ── Step 1: Aggregate ──────────────────────────────────────────────────────

def step_aggregate(min_score: int = 50, threshold: int = 85) -> list[dict]:
    """Run pain_aggregator and return directions."""
    tg_send("🔬 *产研虾启动* — Step 1/7: 正在从 Supabase 聚合痛点数据...")

    output_path = str(OUTPUT_DIR / "pain-aggregation-report.md")
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "pain_aggregator.py"),
        "--min-score", str(min_score),
        "--threshold", str(threshold),
        "--output", output_path,
    ]
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        tg_send(f"❌ 痛点聚合失败:\n```\n{result.stderr[:500]}\n```")
        return []

    # Read JSON output
    json_path = output_path.replace(".md", ".json")
    if os.path.exists(json_path):
        with open(json_path, "r") as f:
            directions = json.load(f)
        tg_send(f"✅ Step 1 完成 — 发现 {len(directions)} 个应用方向")
        return directions

    tg_send("⚠️ 聚合完成但无 JSON 输出")
    return []


# ── Step 2: Wait for GO ───────────────────────────────────────────────────

def step_wait_for_go(directions: list[dict]) -> list[dict]:
    """Wait for user to confirm which directions to GO."""
    if not directions:
        return []

    reply = tg_wait_for_reply("GO", timeout_minutes=120)
    if not reply:
        tg_send("⏰ 超时未收到确认，产研虾暂停")
        return []

    # Parse "GO 1,2" or "GO 1"
    import re
    nums = re.findall(r'\d+', reply)
    selected = []
    for n in nums:
        idx = int(n) - 1
        if 0 <= idx < len(directions):
            selected.append(directions[idx])

    if not selected:
        tg_send(f"⚠️ 未匹配到有效方向编号: {reply}")
        return []

    names = ", ".join(d.get("name", "?") for d in selected)
    tg_send(f"🟢 确认 GO: {names}\nStep 2/7 完成 — 进入 Demo 生成")
    return selected


# ── Step 3: Generate Demo ─────────────────────────────────────────────────

def step_generate_demo(direction: dict) -> str | None:
    """Generate a beautiful Demo HTML for a direction using Claude."""
    name = direction.get("name", "Product")
    one_liner = direction.get("one_liner", "")
    target = direction.get("target_user", "")
    mvp = direction.get("mvp_scope", "")
    problem = direction.get("problem_statement", "")
    value = direction.get("value_proposition", "")

    tg_send(f"🎨 Step 3/7: 为 *{name}* 生成 Demo UI...")

    prompt = f"""You are a senior product designer + frontend engineer.
Generate a BEAUTIFUL, MODERN, single-file HTML demo for this product.

Product: {name}
One-liner: {one_liner}
Target User: {target}
Problem: {problem}
Value: {value}
MVP Scope: {mvp}

Requirements:
1. Single HTML file with Tailwind CDN + Alpine.js for interactivity
2. Dark theme, modern design (like Linear/Raycast aesthetic)
3. At least 3 pages/states: Landing/Hero, Core Feature, Dashboard/Settings
4. Use TAB navigation or sidebar to switch between pages (all in one file)
5. Realistic mock data (not Lorem ipsum — use data that matches the product)
6. All interactive states: hover, active, loading skeletons, empty states
7. Mobile-responsive (375px to 1440px)
8. Color scheme: Max 3 colors, professional
9. Typography: Inter font, clean hierarchy
10. Include micro-interactions (transitions, hover effects)

IMPORTANT:
- This must look PROFESSIONALLY DESIGNED, not like a Bootstrap template
- Include actual UI elements: data tables, charts (CSS-only), cards, forms
- Make it feel like a real product demo, not a wireframe

Output the complete HTML file. Nothing else."""

    resp = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    html = resp.content[0].text.strip()

    # Clean up if wrapped in markdown
    if html.startswith("```"):
        lines = html.split("\n")
        html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        if html.startswith("html"):
            html = html[4:].strip()

    # Save demo
    slug = name.lower().replace(" ", "-")[:30]
    demo_dir = DEMO_DIR / slug
    demo_dir.mkdir(parents=True, exist_ok=True)
    demo_path = demo_dir / "index.html"
    demo_path.write_text(html, encoding="utf-8")

    log.info("Demo saved: %s (%d bytes)", demo_path, len(html))

    # Make accessible via static server
    demo_url = f"{DEMO_BASE_URL}/{slug}/"
    tg_send(
        f"✅ Step 3 完成 — Demo 已生成\n"
        f"📁 路径: `{demo_path}`\n"
        f"🌐 预览: {demo_url}\n\n"
        f"检查后回复:\n"
        f"• `OK` — 继续生成文章+GitHub\n"
        f"• 发截图+描述 — 我来修改"
    )
    return str(demo_path)


# ── Step 4: Handle feedback loop ──────────────────────────────────────────

def step_feedback_loop(demo_path: str, direction: dict, max_rounds: int = 3) -> bool:
    """Wait for user feedback, iterate on demo if needed."""
    for round_num in range(max_rounds):
        reply = tg_wait_for_reply("", timeout_minutes=30)
        if not reply:
            tg_send("⏰ 超时未收到反馈，默认 OK 继续")
            return True

        if reply.upper() in ("OK", "好", "可以", "确认", "没问题", "LGTM"):
            tg_send(f"✅ Step 4 完成 — Demo 确认通过 (第{round_num + 1}轮)")
            return True

        # User has feedback — regenerate
        tg_send(f"🔄 收到反馈 (第{round_num + 1}轮): 正在修改...")

        # Read current demo
        current_html = Path(demo_path).read_text(encoding="utf-8")

        fix_prompt = f"""The user reviewed the demo and has this feedback:
"{reply}"

Current HTML (first 8000 chars):
{current_html[:8000]}

Fix the issues described in the feedback. Output the COMPLETE updated HTML file.
Maintain the same quality and design system. Only fix what the user mentioned."""

        resp = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=16000,
            messages=[{"role": "user", "content": fix_prompt}],
        )
        new_html = resp.content[0].text.strip()
        if new_html.startswith("```"):
            lines = new_html.split("\n")
            new_html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if new_html.startswith("html"):
                new_html = new_html[4:].strip()

        Path(demo_path).write_text(new_html, encoding="utf-8")
        slug = direction.get("name", "").lower().replace(" ", "-")[:30]
        demo_url = f"{DEMO_BASE_URL}/{slug}/"
        tg_send(f"✅ 修改完成 — 请检查: {demo_url}\n回复 `OK` 确认 或继续发反馈")

    tg_send("⚠️ 已达最大修改轮次(3次)，继续下一步")
    return True


# ── Step 5: Generate article ──────────────────────────────────────────────

def step_generate_article(direction: dict) -> str | None:
    """Generate Deep Research article."""
    name = direction.get("name", "Product")
    tg_send(f"📝 Step 5/7: 为 *{name}* 生成 Deep Research 公众号文章...")

    output_dir = str(OUTPUT_DIR / "articles")
    # Save direction data for the writer
    input_json = str(OUTPUT_DIR / "current-direction.json")
    with open(input_json, "w", encoding="utf-8") as f:
        json.dump([direction], f, ensure_ascii=False, indent=2)

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "deep_research_writer.py"),
        "--direction", name,
        "--input", input_json,
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        tg_send(f"❌ 文章生成失败:\n```\n{result.stderr[:500]}\n```")
        return None

    tg_send(f"✅ Step 5 完成 — 文章已生成\n📁 目录: `{output_dir}`")
    return output_dir


# ── Step 6: GitHub publish ────────────────────────────────────────────────

def step_github_publish(direction: dict) -> str | None:
    """Generate GitHub README + Landing Page."""
    name = direction.get("name", "Product")
    one_liner = direction.get("one_liner", "")
    features = [direction.get("value_proposition", one_liner)]
    mvp = direction.get("mvp_scope", "")
    if mvp:
        features.extend(mvp.split(";")[:3])

    tg_send(f"🚀 Step 6/7: 为 *{name}* 生成 GitHub 包装...")

    output_dir = str(OUTPUT_DIR / "github" / name.lower().replace(" ", "-")[:30])
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "github_publisher.py"),
        "--name", name,
        "--desc", one_liner,
        "--features", *features[:5],
        "--local", output_dir,
        "--prepare",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        tg_send(f"❌ GitHub 包装失败:\n```\n{result.stderr[:500]}\n```")
        return None

    tg_send(f"✅ Step 6 完成 — README + Landing Page 已生成\n📁 目录: `{output_dir}`")
    return output_dir


# ── Step 7: Final summary ────────────────────────────────────────────────

def step_final_summary(direction: dict, demo_path: str, article_dir: str | None, github_dir: str | None) -> None:
    """Send final summary with all links."""
    name = direction.get("name", "Product")
    slug = name.lower().replace(" ", "-")[:30]

    summary = (
        f"🎉 *产研虾全链路完成 — {name}*\n\n"
        f"📌 {direction.get('one_liner', '')}\n\n"
        f"*产出清单:*\n"
        f"1. 🎨 Demo: `{demo_path}`\n"
        f"   预览: {DEMO_BASE_URL}/{slug}/\n"
    )

    if article_dir:
        summary += f"2. 📝 文章: `{article_dir}/`\n"
    if github_dir:
        summary += f"3. 🚀 GitHub: `{github_dir}/`\n"

    summary += (
        f"\n*下一步:*\n"
        f"• `/build-mvp {name}` — 启动正式 MVP 构建\n"
        f"• 文章可用 Mdnice 排版后发公众号\n"
        f"• GitHub 文件可直接 push 到仓库\n"
    )

    tg_send(summary)
    log.info("Pipeline complete for: %s", name)


# ── Main Orchestration ────────────────────────────────────────────────────

def run_pipeline(step: str = "", direction_name: str = "", auto: bool = False):
    """Run the full research pipeline or a specific step."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    if step == "aggregate" or not step:
        # Step 1: Aggregate
        directions = step_aggregate()
        if not directions:
            return

        if step == "aggregate":
            return  # Just aggregate, don't continue

        # Step 2: Wait for GO (or auto-select)
        if auto:
            go_directions = [d for d in directions if "GO" in d.get("go_or_kill_recommendation", "").upper()]
            if not go_directions:
                go_directions = directions[:1]  # Take top-scoring
            tg_send(f"🤖 自动模式 — 选择 {len(go_directions)} 个 GO 候选")
        else:
            go_directions = step_wait_for_go(directions)

        if not go_directions:
            return

    elif step == "demo":
        # Skip to demo with a specific direction
        go_directions = [{"name": direction_name, "one_liner": direction_name}]
    else:
        log.error("Unknown step: %s", step)
        return

    # Process each confirmed direction
    for i, direction in enumerate(go_directions):
        name = direction.get("name", "Product")
        tg_send(f"━━━ 处理方向 {i+1}/{len(go_directions)}: *{name}* ━━━")

        # Step 3: Generate Demo
        demo_path = step_generate_demo(direction)
        if not demo_path:
            continue

        # Step 4: Feedback loop (skip in auto mode)
        if not auto:
            step_feedback_loop(demo_path, direction)

        # Step 5: Generate article
        article_dir = step_generate_article(direction)

        # Step 6: GitHub publish
        github_dir = step_github_publish(direction)

        # Step 7: Final summary
        step_final_summary(direction, demo_path, article_dir, github_dir)

    tg_send(f"🏁 *产研虾全链路完成* — 共处理 {len(go_directions)} 个方向")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Research Pipeline — 产研虾全链路")
    parser.add_argument("--step", type=str, default="", choices=["", "aggregate", "demo"],
                        help="Run specific step only")
    parser.add_argument("--direction", type=str, default="", help="Direction name (for --step demo)")
    parser.add_argument("--auto", action="store_true", help="Full auto mode (no user confirmation)")
    parser.add_argument("--min-score", type=int, default=50, help="Min cluster score")
    parser.add_argument("--threshold", type=int, default=85, help="GO threshold")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Research Pipeline — 产研虾全链路")
    log.info("=" * 60)

    run_pipeline(step=args.step, direction_name=args.direction, auto=args.auto)


if __name__ == "__main__":
    main()
