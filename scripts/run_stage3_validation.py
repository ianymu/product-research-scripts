"""
V7 Pipeline — Stage 3 Market Validation Orchestrator
One-click entry: TAM → Competitors (parallel) → LP + Vercel → Capital Assessment → Gate Check → Report → TG Push

Usage:
    python3 run_stage3_validation.py \
        --cycle 2001 \
        --direction "独立创业者社群平台" \
        --competitors "Indie Hackers,WIP.co,Focusmate,Lemon Squeezy,YC Startup School"

    # After 24h, collect LP signup data:
    python3 run_stage3_validation.py --cycle 2001 --collect-lp
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from supabase import create_client

# — Environment —
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
TG_BOT_TOKEN = os.environ.get("TG_TOKEN_MARKET", "").strip()
TG_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "").strip()

DIRECTION_ID = "solopreneur-community"

# — Import sibling scripts —
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from tam_calc import calculate_tam
from competitor_report import analyze_competitor
from landing_page_gen import generate_and_deploy
from capital_assessment import run_assessment, format_summary


# ============================================================
# Telegram
# ============================================================

def tg_send(text: str) -> None:
    """Send message to Telegram group."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(f"[TG skip] {text[:80]}...")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG error] {e}", file=sys.stderr)


# ============================================================
# Pipeline Status
# ============================================================

def update_pipeline_status(sb, cycle_id: int, stage: str,
                           status: str, details: str = "") -> None:
    """Insert pipeline_status row (no unique constraint, so always insert)."""
    sb.table("pipeline_status").insert({
        "cycle_id": cycle_id,
        "direction_id": DIRECTION_ID,
        "stage": stage,
        "status": status,
        "metadata": json.dumps({"details": details}) if details else None,
        "started_at": datetime.now(timezone.utc).isoformat() if status == "running" else None,
        "completed_at": datetime.now(timezone.utc).isoformat() if status in ("completed", "failed") else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


# ============================================================
# Step 1: TAM Research
# ============================================================

def step_tam(cycle_id: int, direction_name: str) -> dict:
    """Run TAM/SAM/SOM research."""
    print("\n[1/6] TAM/SAM/SOM Research...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    update_pipeline_status(sb, cycle_id, "stage3_tam", "running")

    try:
        result = calculate_tam(cycle_id, DIRECTION_ID, direction_name)
        tam_raw = result.get("tam", {}).get("value", 0)
        # Handle string values like "$15B" or "15000000000"
        if isinstance(tam_raw, str):
            tam_raw = tam_raw.replace(",", "").replace("$", "")
            multipliers = {"B": 1e9, "b": 1e9, "M": 1e6, "m": 1e6, "K": 1e3, "k": 1e3}
            for suffix, mult in multipliers.items():
                if tam_raw.endswith(suffix):
                    tam_raw = float(tam_raw[:-1]) * mult
                    break
            tam_raw = float(tam_raw) if tam_raw else 0
        tam_val = float(tam_raw or 0)
        trend = result.get("trend", "unknown")
        print(f"  TAM: ${tam_val:,.0f} | Trend: {trend}")
        update_pipeline_status(sb, cycle_id, "stage3_tam", "completed",
                               f"TAM=${tam_val:,.0f}, trend={trend}")
        return {"success": True, "data": result}
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        update_pipeline_status(sb, cycle_id, "stage3_tam", "failed", str(e))
        return {"success": False, "error": str(e)}


# ============================================================
# Step 2: Competitor Analysis (parallel)
# ============================================================

def step_competitors(cycle_id: int, competitors: list[str]) -> dict:
    """Run competitor analysis in parallel."""
    print(f"\n[2/6] Competitor Analysis ({len(competitors)} competitors, parallel)...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    update_pipeline_status(sb, cycle_id, "stage3_competitors", "running")

    results = {}
    errors = []

    def analyze_one(name: str) -> tuple[str, dict]:
        print(f"  Analyzing: {name}...")
        return name, analyze_competitor(cycle_id, DIRECTION_ID, name)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(analyze_one, c): c for c in competitors}
        for future in as_completed(futures):
            comp_name = futures[future]
            try:
                name, data = future.result()
                results[name] = data
                print(f"  Done: {name}")
            except Exception as e:
                errors.append(f"{comp_name}: {e}")
                print(f"  FAILED: {comp_name} — {e}", file=sys.stderr)

    status = "completed" if not errors else "partial"
    update_pipeline_status(sb, cycle_id, "stage3_competitors", status,
                           f"{len(results)} OK, {len(errors)} failed")
    return {"success": len(results) > 0, "data": results, "errors": errors}


# ============================================================
# Step 3: Landing Page + Vercel Deploy
# ============================================================

def step_landing_page(direction_name: str) -> dict:
    """Generate LP and deploy to Vercel."""
    print("\n[3/6] Landing Page Generation + Vercel Deploy...")

    features = [
        {"title": "Accountability Partners",
         "desc": "Get matched with a founder at your stage. Weekly check-ins keep you on track."},
        {"title": "Growth Playbooks",
         "desc": "Crowdsourced tactics from founders who've done it. No theory, just what works."},
        {"title": "Build in Public",
         "desc": "Share progress, get feedback, attract your first users from the community."},
        {"title": "Revenue Milestones",
         "desc": "Track MRR goals together. Celebrate $1K, $10K, $100K with your cohort."},
        {"title": "AI Co-pilot",
         "desc": "AI-powered suggestions based on what worked for similar products in your niche."},
        {"title": "Founder Matching",
         "desc": "Find co-founders, advisors, or beta testers. Filtered by stage, niche, and timezone."},
    ]

    value_prop = (
        "The accountability + community + growth platform for indie founders. "
        "Stop building alone — join a tribe that holds you accountable, "
        "shares growth tactics, and celebrates your wins."
    )

    try:
        result = generate_and_deploy(
            direction_name="Solopreneur OS",
            value_prop=value_prop,
            features=features,
            project_name="v7-solopreneur-os",
            deploy=True,
        )
        url = result.get("url", "")
        print(f"  LP URL: {url or '(local only)'}")
        return {"success": True, "data": result}
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


# ============================================================
# Step 4: Capital Assessment
# ============================================================

def step_capital(cycle_id: int, direction_name: str) -> dict:
    """Run Scorecard + VC Valuation + Thiel Test."""
    print("\n[4/6] Capital Assessment (Scorecard + VC + Thiel)...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    update_pipeline_status(sb, cycle_id, "stage3_capital", "running")

    try:
        result = run_assessment(cycle_id, DIRECTION_ID, direction_name)
        summary = format_summary(result)
        print(f"  {summary}")
        update_pipeline_status(sb, cycle_id, "stage3_capital", "completed",
                               summary[:500])
        return {"success": True, "data": result}
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        update_pipeline_status(sb, cycle_id, "stage3_capital", "failed", str(e))
        return {"success": False, "error": str(e)}


# ============================================================
# Step 5: Gate Check
# ============================================================

def step_gate_check(tam_result: dict, capital_result: dict,
                    lp_url: str) -> dict:
    """Evaluate 5 hard gates."""
    print("\n[5/6] Gate Check (5 Hard Gates)...")

    gates = {}

    # Gate 1: TAM >= $1B
    tam_val = tam_result.get("data", {}).get("tam", {}).get("value", 0)
    if isinstance(tam_val, str):
        cleaned = tam_val.replace(",", "").replace("$", "").strip()
        multipliers = {"B": 1e9, "b": 1e9, "M": 1e6, "m": 1e6, "K": 1e3, "k": 1e3}
        for suffix, mult in multipliers.items():
            if cleaned.endswith(suffix):
                cleaned = str(float(cleaned[:-1]) * mult)
                break
        tam_val = float(cleaned) if cleaned else 0
    tam_val = float(tam_val or 0)
    gates["tam"] = {
        "threshold": "$1B",
        "actual": f"${tam_val:,.0f}" if tam_val else "N/A",
        "pass": tam_val >= 1_000_000_000 if tam_val else False,
        "fail_action": "KILL (unless D1-D8 >= 50/60)",
    }

    # Gate 2: Trend = accelerating or structural window
    trend = tam_result.get("data", {}).get("trend", "unknown")
    gates["trend"] = {
        "threshold": "accelerating or structural window",
        "actual": trend,
        "pass": trend in ("accelerating", "structural_window"),
        "fail_action": "MAYBE",
    }

    # Gate 3: LP signup rate >= 3% (deferred — needs 24h data)
    gates["lp_signup_rate"] = {
        "threshold": ">=3%",
        "actual": "PENDING (need 24h traffic data)",
        "pass": None,  # Cannot determine yet
        "fail_action": "Degrade or rewrite copy",
        "note": f"LP deployed at: {lp_url}" if lp_url else "LP not deployed",
    }

    # Gate 4: Scorecard >= 100% (weighted_pct)
    sc = capital_result.get("data", {}).get("scorecard", {})
    sc_pct = sc.get("weighted_pct", "0%")
    sc_num = float(sc_pct.replace("%", "")) if isinstance(sc_pct, str) else 0
    gates["scorecard"] = {
        "threshold": ">=100%",
        "actual": sc_pct,
        "pass": sc_num >= 100,
        "fail_action": "Capital interest weak",
    }

    # Gate 5: Thiel >= 3/4
    thiel = capital_result.get("data", {}).get("thiel_test", {})
    thiel_score = thiel.get("score", 0)
    gates["thiel"] = {
        "threshold": ">=3/4",
        "actual": f"{thiel_score}/4",
        "pass": thiel_score >= 3,
        "fail_action": "No moat, easy to copy",
    }

    # Summary
    passed = sum(1 for g in gates.values() if g["pass"] is True)
    failed = sum(1 for g in gates.values() if g["pass"] is False)
    pending = sum(1 for g in gates.values() if g["pass"] is None)

    verdict = "LOCK" if failed == 0 and pending == 0 else (
        "PENDING" if pending > 0 and failed == 0 else
        "MAYBE" if failed <= 1 else "KILL"
    )

    result = {
        "gates": gates,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "verdict": verdict,
    }

    for name, g in gates.items():
        icon = "PASS" if g["pass"] else ("PENDING" if g["pass"] is None else "FAIL")
        print(f"  [{icon}] {name}: {g['actual']} (threshold: {g['threshold']})")
    print(f"  VERDICT: {verdict} ({passed} pass, {failed} fail, {pending} pending)")

    return result


# ============================================================
# Step 6: Generate Report
# ============================================================

def step_generate_report(cycle_id: int, direction_name: str,
                         tam_result: dict, comp_result: dict,
                         lp_result: dict, capital_result: dict,
                         gate_result: dict) -> str:
    """Generate comprehensive Stage 3 report as Markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    tam_data = tam_result.get("data", {})
    capital_data = capital_result.get("data", {})
    sc = capital_data.get("scorecard", {})
    vc = capital_data.get("vc_valuation", {})
    thiel = capital_data.get("thiel_test", {})
    verdict = capital_data.get("overall_verdict", {})
    gates = gate_result.get("gates", {})

    # Competitor summaries
    comp_lines = []
    for name, data in comp_result.get("data", {}).items():
        thiel_c = data.get("thiel_comparison", {})
        if isinstance(thiel_c, str):
            thiel_c = json.loads(thiel_c) if thiel_c else {}
        funding = float(data.get("total_funding") or 0)
        strengths = data.get("strengths", []) or []
        weaknesses = data.get("weaknesses", []) or []
        if isinstance(strengths, str):
            strengths = json.loads(strengths) if strengths else []
        if isinstance(weaknesses, str):
            weaknesses = json.loads(weaknesses) if weaknesses else []
        tech_y = "Y" if thiel_c.get("proprietary_tech", {}).get("has") else "N"
        net_y = "Y" if thiel_c.get("network_effects", {}).get("has") else "N"
        scale_y = "Y" if thiel_c.get("economies_of_scale", {}).get("has") else "N"
        brand_y = "Y" if thiel_c.get("brand", {}).get("has") else "N"
        comp_lines.append(
            f"### {name}\n"
            f"- **Strengths**: {', '.join(strengths[:3])}\n"
            f"- **Weaknesses**: {', '.join(weaknesses[:3])}\n"
            f"- **Adoption Stage**: {data.get('adoption_stage', 'N/A')}\n"
            f"- **Total Funding**: ${funding:,.0f}\n"
            f"- **Thiel**: Tech={tech_y} | Network={net_y} | Scale={scale_y} | Brand={brand_y}\n"
            f"- **Differentiation**: {data.get('differentiation_angle', 'N/A')}\n"
        )

    # Gate summary
    gate_lines = []
    for gname, g in gates.items():
        icon = "PASS" if g["pass"] else ("PENDING" if g["pass"] is None else "FAIL")
        gate_lines.append(f"| {gname} | {g['threshold']} | {g['actual']} | {icon} |")
    gate_table = "\n".join(gate_lines)

    # Competitor section
    comp_section = "".join(comp_lines) if comp_lines else "No competitor data."

    # TAM values (safe formatting)
    tam_dict = tam_data.get("tam", {})
    sam_dict = tam_data.get("sam", {})
    som_dict = tam_data.get("som", {})
    tam_val_fmt = f"${tam_dict.get('value', 0):,}" if tam_dict.get("value") else "N/A"
    sam_val_fmt = f"${sam_dict.get('value', 0):,}" if sam_dict.get("value") else "N/A"
    som_val_fmt = f"${som_dict.get('value', 0):,}" if som_dict.get("value") else "N/A"

    # VC values (safe formatting)
    y5_rev = vc.get("y5_revenue_estimate", 0)
    y5_rev_fmt = f"${y5_rev:,}" if isinstance(y5_rev, (int, float)) else str(y5_rev)
    exit_val = vc.get("exit_value", 0)
    exit_val_fmt = f"${exit_val:,}" if isinstance(exit_val, (int, float)) else str(exit_val)
    premoney = vc.get("pre_money_valuation", 0)
    premoney_fmt = f"${premoney:,}" if isinstance(premoney, (int, float)) else str(premoney)

    # Thiel booleans
    thiel_tech = "Yes" if thiel.get("proprietary_tech", {}).get("has") else "No"
    thiel_net = "Yes" if thiel.get("network_effects", {}).get("has") else "No"
    thiel_scale = "Yes" if thiel.get("economies_of_scale", {}).get("has") else "No"
    thiel_brand = "Yes" if thiel.get("brand", {}).get("has") else "No"
    investable = "Yes" if verdict.get("investable") else "No"

    # Next steps
    next_steps = []
    v = gate_result.get("verdict", "")
    if v == "LOCK":
        next_steps.append("- **LOCK**: Proceed to Stage 4 (Business Design)")
    if gate_result.get("pending", 0) > 0:
        next_steps.append(
            f"- **PENDING**: Wait for LP signup data (24h). "
            f"Then run: `python3 run_stage3_validation.py --cycle {cycle_id} --collect-lp`"
        )
    if v == "MAYBE":
        next_steps.append("- **MAYBE**: Review failed gates. Consider re-running LP with different copy.")
    if v == "KILL":
        next_steps.append("- **KILL**: Direction does not pass capital gates. Consider pivoting.")
    next_steps_text = "\n".join(next_steps)

    lp_url_display = lp_result.get("data", {}).get("url", "Not deployed")
    lp_local_display = lp_result.get("data", {}).get("local_path", "N/A")

    report = f"""# Stage 3 市场验证报告 — {direction_name}

> **Cycle**: {cycle_id}
> **Direction ID**: {DIRECTION_ID}
> **Generated**: {now}
> **Gate Verdict**: **{gate_result.get('verdict', 'N/A')}** ({gate_result.get('passed', 0)} pass / {gate_result.get('failed', 0)} fail / {gate_result.get('pending', 0)} pending)

---

## 1. TAM/SAM/SOM

| Metric | Value | Source |
|--------|-------|--------|
| TAM | {tam_val_fmt} | {tam_dict.get('source', 'N/A')} |
| SAM | {sam_val_fmt} | {sam_dict.get('source', 'N/A')} |
| SOM | {som_val_fmt} | {som_dict.get('source', 'N/A')} |

**Trend**: {tam_data.get('trend', 'N/A')} | **Confidence**: {tam_data.get('confidence', 'N/A')}

**TAM Reasoning**: {tam_dict.get('reasoning', 'N/A')}

---

## 2. Competitor Analysis ({len(comp_result.get('data', {}))} competitors)

{comp_section}

---

## 3. Landing Page

- **URL**: {lp_url_display}
- **Local**: {lp_local_display}
- **Signup Rate**: PENDING (need 24h traffic data)

---

## 4. Capital Assessment

### 4.1 Scorecard

| Factor | Weight | Score (1-5) | Reasoning |
|--------|--------|-------------|-----------|
| Team | 30% | {sc.get('team', {}).get('score', '?')} | {sc.get('team', {}).get('reasoning', '')} |
| Market | 25% | {sc.get('market', {}).get('score', '?')} | {sc.get('market', {}).get('reasoning', '')} |
| Product | 15% | {sc.get('product', {}).get('score', '?')} | {sc.get('product', {}).get('reasoning', '')} |
| Competition | 10% | {sc.get('competition', {}).get('score', '?')} | {sc.get('competition', {}).get('reasoning', '')} |
| Marketing | 10% | {sc.get('marketing', {}).get('score', '?')} | {sc.get('marketing', {}).get('reasoning', '')} |
| Fund/Other | 10% | {sc.get('fundraising_other', {}).get('score', '?')} | {sc.get('fundraising_other', {}).get('reasoning', '')} |

**Weighted Total**: {sc.get('weighted_pct', 'N/A')}

### 4.2 VC Valuation

- **Y5 Revenue**: {y5_rev_fmt}
- **PS Multiple**: {vc.get('ps_multiple', 'N/A')}x
- **Comparable Exit**: {vc.get('comparable_exit', 'N/A')}
- **Exit Value**: {exit_val_fmt}
- **Pre-Money Valuation**: {premoney_fmt}

### 4.3 Thiel Monopoly Test

| Pillar | Has? | Reasoning |
|--------|------|-----------|
| Proprietary Tech | {thiel_tech} | {thiel.get('proprietary_tech', {}).get('reasoning', '')} |
| Network Effects | {thiel_net} | {thiel.get('network_effects', {}).get('reasoning', '')} |
| Economies of Scale | {thiel_scale} | {thiel.get('economies_of_scale', {}).get('reasoning', '')} |
| Brand | {thiel_brand} | {thiel.get('brand', {}).get('reasoning', '')} |

**Thiel Score**: {thiel.get('score', 'N/A')}/4 — {thiel.get('verdict', 'N/A')}

### 4.4 Overall Verdict

- **Investable**: {investable}
- **Summary**: {verdict.get('summary', 'N/A')}
- **Key Risk**: {verdict.get('key_risk', 'N/A')}
- **Key Strength**: {verdict.get('key_strength', 'N/A')}

---

## 5. Hard Gate Summary

| Gate | Threshold | Actual | Result |
|------|-----------|--------|--------|
{gate_table}

**Final Verdict**: **{gate_result.get('verdict', 'N/A')}**

---

## 6. Next Steps

{next_steps_text}
"""

    # Save report
    report_dir = os.path.join(os.path.dirname(__file__), "..", "..", "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(
        report_dir,
        f"stage3-cycle-{cycle_id}-{DIRECTION_ID}.md"
    )
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\n[6/6] Report saved: {report_path}")

    return report_path


# ============================================================
# Step 7: TG Push
# ============================================================

def step_tg_push(cycle_id: int, direction_name: str,
                 gate_result: dict, lp_url: str,
                 tam_result: dict, capital_result: dict) -> None:
    """Push gate results to Telegram."""
    print("\n[7/7] Pushing to Telegram...")

    gates = gate_result.get("gates", {})
    verdict = gate_result.get("verdict", "N/A")
    tam_val = tam_result.get("data", {}).get("tam", {}).get("value", 0)
    trend = tam_result.get("data", {}).get("trend", "?")

    thiel = capital_result.get("data", {}).get("thiel_test", {})
    thiel_score = thiel.get("score", 0)
    sc_pct = capital_result.get("data", {}).get("scorecard", {}).get(
        "weighted_pct", "N/A")

    gate_icons = []
    for gname, g in gates.items():
        icon = "\u2705" if g["pass"] else ("\u23f3" if g["pass"] is None else "\u274c")
        gate_icons.append(f"  {icon} {gname}: {g['actual']}")

    gates_text = "\n".join(gate_icons)
    lp_line = "LP: " + lp_url if lp_url else "LP: not deployed"
    pending_line = "24h后运行 --collect-lp 补充LP注册率数据" if gate_result.get("pending", 0) > 0 else ""

    msg = (
        f"<b>\U0001f3af Stage 3 Gate Check \u2014 {direction_name}</b>\n"
        f"Cycle: {cycle_id}\n\n"
        f"<b>TAM</b>: ${tam_val:,.0f} ({trend})\n"
        f"<b>Scorecard</b>: {sc_pct}\n"
        f"<b>Thiel</b>: {thiel_score}/4\n\n"
        f"<b>Gates:</b>\n{gates_text}\n\n"
        f"<b>Verdict: {verdict}</b>\n"
        f"{lp_line}\n"
        f"{pending_line}"
    )

    tg_send(msg)
    print("  TG message sent.")


# ============================================================
# LP Collection (post-24h)
# ============================================================

def collect_lp_data(cycle_id: int) -> None:
    """Collect LP signup data after 24h and update gate verdict."""
    print("\n[LP Collection] Fetching signup data from Supabase...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Count signups for this direction
    resp = sb.table("lp_signups").select("*", count="exact").eq(
        "direction", "Solopreneur OS"
    ).execute()
    signups = resp.count or len(resp.data)

    # You'd need to track visitors separately (e.g., via Vercel Analytics)
    # For now, ask user for visitor count
    print(f"  Signups found: {signups}")
    print("  NOTE: Enter total LP visitors (from Vercel Analytics):")
    visitors = int(input("  Visitors: ") or "0")

    if visitors > 0:
        rate = (signups / visitors) * 100
        print(f"  Signup rate: {rate:.1f}% ({signups}/{visitors})")
        passed = rate >= 3.0

        # Update market_validations
        sb.table("market_validations").update({
            "lp_signups": signups,
            "lp_visitors": visitors,
            "lp_signup_rate": round(rate, 2),
            "lp_gate_pass": passed,
        }).eq("cycle_id", cycle_id).eq(
            "direction_id", DIRECTION_ID
        ).execute()

        icon = "\u2705" if passed else "\u274c"
        tg_send(
            f"{icon} <b>LP Gate Update</b> — Cycle {cycle_id}\n"
            f"Signups: {signups} / Visitors: {visitors}\n"
            f"Rate: {rate:.1f}% (threshold: 3%)\n"
            f"Result: {'PASS' if passed else 'FAIL'}"
        )
    else:
        print("  No visitor data. Skipping LP gate update.")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="V7 Stage 3 Validation")
    parser.add_argument("--cycle", type=int, default=2001)
    parser.add_argument("--direction", type=str,
                        default="独立创业者社群平台")
    parser.add_argument("--competitors", type=str,
                        default="Indie Hackers,WIP.co,Focusmate,Lemon Squeezy,YC Startup School")
    parser.add_argument("--collect-lp", action="store_true",
                        help="Collect LP signup data (run 24h after initial)")
    parser.add_argument("--skip-tam", action="store_true")
    parser.add_argument("--skip-competitors", action="store_true")
    parser.add_argument("--skip-lp", action="store_true")
    parser.add_argument("--skip-capital", action="store_true")
    args = parser.parse_args()

    # LP collection mode
    if args.collect_lp:
        collect_lp_data(args.cycle)
        return

    print("=" * 60)
    print(f"V7 Stage 3 — Market Validation")
    print(f"Cycle: {args.cycle} | Direction: {args.direction}")
    print(f"Competitors: {args.competitors}")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    update_pipeline_status(sb, args.cycle, "stage3", "running",
                           f"Started at {datetime.now(timezone.utc).isoformat()}")

    competitors = [c.strip() for c in args.competitors.split(",")]
    results = {}
    start_time = time.time()

    # Step 1: TAM
    if not args.skip_tam:
        results["tam"] = step_tam(args.cycle, args.direction)
    else:
        results["tam"] = {"success": True, "data": {}}
        print("\n[1/6] TAM — SKIPPED")

    # Step 2: Competitors (parallel)
    if not args.skip_competitors:
        results["competitors"] = step_competitors(args.cycle, competitors)
    else:
        results["competitors"] = {"success": True, "data": {}, "errors": []}
        print("\n[2/6] Competitors — SKIPPED")

    # Step 3: Landing Page
    if not args.skip_lp:
        results["lp"] = step_landing_page(args.direction)
    else:
        results["lp"] = {"success": True, "data": {"url": "", "local_path": ""}}
        print("\n[3/6] Landing Page — SKIPPED")

    # Step 4: Capital Assessment
    if not args.skip_capital:
        results["capital"] = step_capital(args.cycle, args.direction)
    else:
        results["capital"] = {"success": True, "data": {}}
        print("\n[4/6] Capital Assessment — SKIPPED")

    # Step 5: Gate Check
    lp_url = results["lp"].get("data", {}).get("url", "")
    results["gates"] = step_gate_check(
        results["tam"], results["capital"], lp_url
    )

    # Step 6: Report
    report_path = step_generate_report(
        args.cycle, args.direction,
        results["tam"], results["competitors"],
        results["lp"], results["capital"],
        results["gates"],
    )

    # Step 7: TG Push
    step_tg_push(
        args.cycle, args.direction, results["gates"],
        lp_url, results["tam"], results["capital"],
    )

    # Final status
    elapsed = time.time() - start_time
    final_verdict = results["gates"].get("verdict", "UNKNOWN")
    update_pipeline_status(sb, args.cycle, "stage3", "completed",
                           f"Verdict: {final_verdict}, elapsed: {elapsed:.0f}s")

    print("\n" + "=" * 60)
    print(f"Stage 3 COMPLETE in {elapsed:.0f}s")
    print(f"Verdict: {final_verdict}")
    print(f"Report: {report_path}")
    if final_verdict == "PENDING":
        print(f"\nNext: Wait 24h, then run:")
        print(f"  python3 {__file__} --cycle {args.cycle} --collect-lp")
    print("=" * 60)


if __name__ == "__main__":
    main()
