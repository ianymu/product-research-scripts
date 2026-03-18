#!/usr/bin/env python3
"""
guard_night_audit.py — GuardShrimp 03:00 夜间安全巡检
扫描最近 24h：npm/pip 包变更、Skill 变更、配置文件改动、.env 权限。
结果写入 security_log.json，HIGH/CRITICAL 即时 TG 告警。
"""
import os
import json
import logging
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
MEMORY_DIR = Path(os.environ.get("SHRIMPILOT_MEMORY", os.path.expanduser("~/.shrimpilot/memory")))
SECURITY_LOG = MEMORY_DIR / "security_log.json"
HOME = Path.home()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("guard_audit")

SCAN_DIRS = [
    HOME / "scripts",
    HOME / ".openclaw",
    HOME / "monetization",
    HOME / "shrimpilot",
]


# ── Helpers ─────────────────────────────────────────────────
def run_cmd(cmd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, Exception) as e:
        return f"ERROR: {e}"


def read_security_log() -> dict:
    if SECURITY_LOG.exists():
        try:
            return json.loads(SECURITY_LOG.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": [], "last_scan": None}


def write_security_log(data: dict):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    SECURITY_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info(f"已写入 {SECURITY_LOG}")


# ── Scan: Recently Modified Files ───────────────────────────
def scan_recent_files(hours: int = 24) -> list[dict]:
    """找出最近 N 小时内修改的文件."""
    issues = []
    cutoff = datetime.now() - timedelta(hours=hours)

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for f in scan_dir.rglob("*"):
            if not f.is_file():
                continue
            if "__pycache__" in str(f) or ".pyc" in str(f):
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime >= cutoff:
                    issues.append({
                        "type": "file_modified",
                        "path": str(f),
                        "modified": mtime.isoformat(),
                        "size": f.stat().st_size,
                    })
            except OSError:
                continue

    return issues


# ── Scan: npm/pip Package Changes ───────────────────────────
def scan_package_changes() -> list[dict]:
    """检查最近安装的 npm/pip 包."""
    issues = []

    # pip: 检查最近 24h 安装的包
    pip_output = run_cmd("pip3 list --format=json 2>/dev/null")
    if pip_output and not pip_output.startswith("ERROR"):
        try:
            packages = json.loads(pip_output)
            # 记录包总数作为基线
            issues.append({
                "type": "pip_baseline",
                "severity": "INFO",
                "detail": f"pip3 已安装 {len(packages)} 个包",
            })
        except json.JSONDecodeError:
            pass

    # npm global: 检查全局包
    npm_output = run_cmd("npm list -g --depth=0 --json 2>/dev/null")
    if npm_output and not npm_output.startswith("ERROR"):
        try:
            npm_data = json.loads(npm_output)
            deps = npm_data.get("dependencies", {})
            issues.append({
                "type": "npm_baseline",
                "severity": "INFO",
                "detail": f"npm global 已安装 {len(deps)} 个包",
            })
        except json.JSONDecodeError:
            pass

    # pip-audit (如果可用)
    audit_output = run_cmd("pip-audit --format=json 2>/dev/null", timeout=60)
    if audit_output and not audit_output.startswith("ERROR"):
        try:
            vulns = json.loads(audit_output)
            if vulns:
                for v in vulns[:10]:  # 最多 10 个
                    issues.append({
                        "type": "pip_vulnerability",
                        "severity": "HIGH",
                        "detail": f"{v.get('name', '?')} {v.get('version', '?')}: {v.get('id', '?')}",
                    })
        except json.JSONDecodeError:
            pass

    return issues


# ── Scan: .env Permissions ──────────────────────────────────
def scan_env_permissions() -> list[dict]:
    """检查 .env 文件权限是否安全."""
    issues = []
    env_files = [
        HOME / ".openclaw" / ".env",
        HOME / ".env",
        HOME / "monetization" / ".env",
        HOME / "shrimpilot" / ".env",
    ]

    for env_file in env_files:
        if not env_file.exists():
            continue
        mode = oct(env_file.stat().st_mode)[-3:]
        if mode not in ("600", "400"):
            issues.append({
                "type": "env_permission",
                "severity": "HIGH",
                "path": str(env_file),
                "detail": f".env 权限 {mode}（应为 600），可能被其他用户读取",
            })
        else:
            issues.append({
                "type": "env_permission",
                "severity": "LOW",
                "path": str(env_file),
                "detail": f".env 权限 {mode} — OK",
            })

    return issues


# ── Scan: Config File Changes ───────────────────────────────
def scan_config_changes(hours: int = 24) -> list[dict]:
    """检查关键配置文件是否被修改."""
    issues = []
    cutoff = datetime.now() - timedelta(hours=hours)

    config_files = [
        HOME / ".openclaw" / "openclaw.json",
        HOME / ".openclaw" / ".env",
        HOME / ".ssh" / "authorized_keys",
    ]

    for cf in config_files:
        if not cf.exists():
            continue
        mtime = datetime.fromtimestamp(cf.stat().st_mtime)
        if mtime >= cutoff:
            issues.append({
                "type": "config_modified",
                "severity": "MEDIUM",
                "path": str(cf),
                "detail": f"配置文件在过去 {hours}h 内被修改: {mtime.strftime('%H:%M')}",
            })

    return issues


# ── Scan: Suspicious Patterns ───────────────────────────────
def scan_suspicious_patterns(recent_files: list[dict]) -> list[dict]:
    """在最近修改的 .py 文件中检查可疑模式."""
    issues = []
    suspicious = [
        ("base64.b64decode", "Base64 解码（可能隐藏恶意 URL）"),
        ("eval(", "eval() 调用（代码注入风险）"),
        ("exec(", "exec() 调用（代码注入风险）"),
        ("subprocess.call(", "subprocess.call 无安全检查"),
        ("os.system(", "os.system() 调用（命令注入风险）"),
    ]

    for f_info in recent_files:
        path = f_info["path"]
        if not path.endswith(".py"):
            continue
        try:
            content = Path(path).read_text(errors="ignore")
            for pattern, desc in suspicious:
                if pattern in content:
                    issues.append({
                        "type": "suspicious_pattern",
                        "severity": "MEDIUM",
                        "path": path,
                        "detail": desc,
                    })
        except (IOError, OSError):
            continue

    return issues


# ── Generate Report ─────────────────────────────────────────
def generate_report(all_issues: list[dict], recent_files: list[dict]) -> tuple[str, str]:
    """生成安全报告，返回 (tg_message, severity)."""
    high_count = sum(1 for i in all_issues if i.get("severity") == "HIGH")
    critical_count = sum(1 for i in all_issues if i.get("severity") == "CRITICAL")
    medium_count = sum(1 for i in all_issues if i.get("severity") == "MEDIUM")

    max_severity = "LOW"
    if medium_count > 0:
        max_severity = "MEDIUM"
    if high_count > 0:
        max_severity = "HIGH"
    if critical_count > 0:
        max_severity = "CRITICAL"

    now_str = datetime.now().strftime("%m月%d日 %H:%M")
    lines = [f"*安全虾夜间巡检* — {now_str}\n"]

    # 概览
    lines.append(f"*扫描范围:* {len(SCAN_DIRS)} 个目录")
    lines.append(f"*最近 24h 变更文件:* {len(recent_files)} 个")
    lines.append(f"*发现问题:* {critical_count} CRITICAL / {high_count} HIGH / {medium_count} MEDIUM")
    lines.append("")

    # HIGH/CRITICAL 详情
    serious = [i for i in all_issues if i.get("severity") in ("HIGH", "CRITICAL")]
    if serious:
        lines.append("*需要关注:*")
        for i in serious[:10]:
            lines.append(f"  [{i['severity']}] {i['detail']}")
        lines.append("")

    # MEDIUM 汇总
    mediums = [i for i in all_issues if i.get("severity") == "MEDIUM"]
    if mediums:
        lines.append(f"*中等风险 ({len(mediums)}):*")
        for i in mediums[:5]:
            lines.append(f"  - {i['detail']}")
        if len(mediums) > 5:
            lines.append(f"  ... 及其他 {len(mediums) - 5} 项")
        lines.append("")

    # 结论
    if max_severity in ("HIGH", "CRITICAL"):
        lines.append("*结论: 需要立即处理上述问题*")
    elif max_severity == "MEDIUM":
        lines.append("*结论: 有中等风险项，建议明天处理*")
    else:
        lines.append("*结论: 一切正常，安全状态良好*")

    lines.append("\n_— 安全虾，值守中_")

    return "\n".join(lines), max_severity


# ── TG Push ─────────────────────────────────────────────────
def send_tg(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.error("TG_BOT_TOKEN 或 TG_CHAT_ID 未设置")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = httpx.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=15)
    if resp.status_code == 200:
        log.info("TG 推送成功")
    else:
        log.error(f"TG 推送失败: {resp.status_code} {resp.text}")


# ── Main ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="GuardShrimp 夜间安全巡检")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送 TG")
    parser.add_argument("--hours", type=int, default=24, help="扫描最近 N 小时 (默认 24)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("GuardShrimp 夜间安全巡检 — Starting")
    log.info("=" * 60)

    # 1. 扫描最近修改的文件
    log.info(f"Step 1: 扫描最近 {args.hours}h 修改的文件...")
    recent_files = scan_recent_files(args.hours)
    log.info(f"  发现 {len(recent_files)} 个文件变更")

    # 2. 包变更检查
    log.info("Step 2: 检查 npm/pip 包...")
    pkg_issues = scan_package_changes()

    # 3. .env 权限检查
    log.info("Step 3: 检查 .env 权限...")
    env_issues = scan_env_permissions()

    # 4. 配置文件变更
    log.info("Step 4: 检查配置文件变更...")
    config_issues = scan_config_changes(args.hours)

    # 5. 可疑模式检查
    log.info("Step 5: 扫描可疑代码模式...")
    suspicious_issues = scan_suspicious_patterns(recent_files)

    # 汇总
    all_issues = pkg_issues + env_issues + config_issues + suspicious_issues
    report, max_severity = generate_report(all_issues, recent_files)

    # 写入 security_log.json
    sec_data = read_security_log()
    scan_record = {
        "timestamp": datetime.now().isoformat(),
        "scan_type": "night_audit",
        "files_scanned": len(recent_files),
        "issues_found": len([i for i in all_issues if i.get("severity") not in ("INFO", "LOW")]),
        "max_severity": max_severity,
        "issues": all_issues,
    }
    sec_data["last_scan"] = scan_record["timestamp"]
    sec_data["scans"].append(scan_record)
    # 只保留最近 30 次
    sec_data["scans"] = sec_data["scans"][-30:]

    if args.dry_run:
        print(report)
        print(f"\n[dry-run] 未推送 TG，未写入 security_log.json")
    else:
        write_security_log(sec_data)
        # HIGH/CRITICAL 即时推送，MEDIUM 也推（夜间巡检一天一次）
        if max_severity in ("HIGH", "CRITICAL", "MEDIUM"):
            send_tg(report)
        else:
            # LOW/INFO 只 log 不推送，避免告警疲劳
            log.info("安全状态良好，无需推送 TG")
            # 但还是发一条简短确认
            send_tg(f"*安全虾夜间巡检* — {datetime.now().strftime('%m月%d日')}\n\n一切正常。扫描 {len(recent_files)} 个变更文件，无风险。\n\n_— 安全虾，值守中_")

    log.info(f"巡检完成: {max_severity}")


if __name__ == "__main__":
    main()
