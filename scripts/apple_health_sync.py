#!/usr/bin/env python3
"""
apple_health_sync.py — Apple Health 数据自动同步

架构：Health Auto Export App (iPhone) → HTTP POST → EC2 Flask → health_log.json
App 配置：REST API URL = https://your-ec2:3001/api/health, Header: api-key: $HAE_WRITE_TOKEN

用法：
  1. EC2 上启动：python3 apple_health_sync.py --serve        # 启动 Flask 接收端
  2. 手动拉取模式：python3 apple_health_sync.py --mock        # 用 mock 数据测试
  3. 分析模式：python3 apple_health_sync.py --analyze          # 分析最新健康数据
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
HEALTH_LOG_DIR = Path(os.path.expanduser("~/.shrimpilot/memory"))
HEALTH_LOG_PATH = HEALTH_LOG_DIR / "health_log.json"
HEALTH_RAW_DIR = HEALTH_LOG_DIR / "raw_health"
STANDARDS_PATH = Path(__file__).parent / "health_standards.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("apple_health_sync")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def ensure_dirs():
    """确保目录存在"""
    HEALTH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_standards():
    return load_json(STANDARDS_PATH, {})


# ---------------------------------------------------------------------------
# 解析 Health Auto Export JSON payload
# ---------------------------------------------------------------------------
def parse_health_payload(payload: dict) -> dict:
    """
    从 Health Auto Export 的 JSON payload 提取 sleep / heart_rate / hrv 数据
    返回标准化的 health_record dict
    """
    metrics = payload.get("data", {}).get("metrics", [])
    hrv_notifs = payload.get("data", {}).get("heartRateNotifications", [])

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "sleep": None,
        "heart_rate": [],
        "resting_heart_rate": None,
        "hrv": [],
    }

    for metric in metrics:
        name = metric.get("name", "")
        data = metric.get("data", [])

        if name == "Sleep Analysis" and data:
            # 取最新的聚合记录
            latest = data[-1]
            record["sleep"] = {
                "date": latest.get("date", ""),
                "total_sleep_min": latest.get("totalSleep") or latest.get("asleep", 0),
                "in_bed_min": latest.get("inBed", 0),
                "deep_min": latest.get("deep", 0),
                "rem_min": latest.get("rem", 0),
                "core_min": latest.get("core", 0),
                "sleep_start": latest.get("sleepStart", ""),
                "sleep_end": latest.get("sleepEnd", ""),
                "in_bed_start": latest.get("inBedStart", ""),
                "in_bed_end": latest.get("inBedEnd", ""),
            }
            # 计算派生指标
            total = record["sleep"]["total_sleep_min"]
            in_bed = record["sleep"]["in_bed_min"]
            if in_bed > 0 and total > 0:
                record["sleep"]["efficiency_pct"] = round(total / in_bed * 100, 1)
                record["sleep"]["deep_pct"] = round(
                    record["sleep"]["deep_min"] / total * 100, 1
                )
                record["sleep"]["rem_pct"] = round(
                    record["sleep"]["rem_min"] / total * 100, 1
                )
                # 睡眠潜伏期 = in_bed_start → sleep_start
                try:
                    ib_start = datetime.strptime(
                        record["sleep"]["in_bed_start"][:19], "%Y-%m-%d %H:%M:%S"
                    )
                    sl_start = datetime.strptime(
                        record["sleep"]["sleep_start"][:19], "%Y-%m-%d %H:%M:%S"
                    )
                    record["sleep"]["latency_min"] = max(
                        0, (sl_start - ib_start).total_seconds() / 60
                    )
                except (ValueError, TypeError):
                    record["sleep"]["latency_min"] = None

        elif name == "Heart Rate" and data:
            for entry in data[-24:]:  # 最近 24 条
                record["heart_rate"].append(
                    {
                        "date": entry.get("date", ""),
                        "min": entry.get("Min"),
                        "avg": entry.get("Avg"),
                        "max": entry.get("Max"),
                    }
                )

        elif name == "Resting Heart Rate" and data:
            latest = data[-1]
            record["resting_heart_rate"] = {
                "date": latest.get("date", ""),
                "bpm": latest.get("Avg") or latest.get("qty"),
            }

    # HRV from heartRateNotifications
    for notif in hrv_notifs[-10:]:
        if "hrv" in notif:
            record["hrv"].append(
                {
                    "value_ms": notif["hrv"],
                    "timestamp": notif.get("timestamp", {}).get("start", ""),
                }
            )

    # HRV 也可能在 metrics 里
    for metric in metrics:
        if metric.get("name") == "Heart Rate Variability":
            for entry in metric.get("data", [])[-10:]:
                record["hrv"].append(
                    {
                        "value_ms": entry.get("Avg") or entry.get("qty"),
                        "timestamp": entry.get("date", ""),
                    }
                )

    return record


# ---------------------------------------------------------------------------
# 写入 health_log.json（合并到现有数据）
# ---------------------------------------------------------------------------
def update_health_log(record: dict):
    """将解析后的 health_record 合并到 health_log.json"""
    ensure_dirs()
    health_log = load_json(HEALTH_LOG_PATH, {})

    today = datetime.utcnow().strftime("%Y-%m-%d")
    health_log["date"] = today
    health_log["last_sync"] = record["timestamp"]

    # 睡眠数据
    if record["sleep"]:
        health_log["sleep_data"] = record["sleep"]
        total_hrs = record["sleep"]["total_sleep_min"] / 60
        health_log["sleep_hours_estimated"] = round(total_hrs, 1)

    # 静息心率
    if record["resting_heart_rate"]:
        health_log["resting_hr_bpm"] = record["resting_heart_rate"]["bpm"]

    # 心率历史（最近 24 条）
    health_log["heart_rate_history"] = record["heart_rate"]

    # HRV
    if record["hrv"]:
        latest_hrv = record["hrv"][-1]
        health_log["hrv_latest_ms"] = latest_hrv["value_ms"]
        health_log["hrv_history"] = record["hrv"]

        # 计算 7 日均值
        hrv_values = [h["value_ms"] for h in record["hrv"] if h["value_ms"] is not None]
        if hrv_values:
            health_log["hrv_7day_avg_ms"] = round(sum(hrv_values) / len(hrv_values), 1)

    save_json(HEALTH_LOG_PATH, health_log)
    log.info("health_log.json 已更新 — 日期: %s", today)
    return health_log


# ---------------------------------------------------------------------------
# 健康分析引擎
# ---------------------------------------------------------------------------
def analyze_health(health_log: dict, user_age: int = 30) -> dict:
    """基于 health_standards.json 分析健康数据，返回告警列表"""
    standards = load_standards()
    if not standards:
        log.warning("health_standards.json 未找到，跳过分析")
        return {"alerts": [], "severity": "unknown"}

    alerts = []
    burnout_signals = 0

    sleep_std = standards.get("sleep", {})
    hr_std = standards.get("heart_rate", {})
    hrv_std = standards.get("hrv", {})
    burnout_std = standards.get("burnout_composite", {})

    # --- 睡眠分析 ---
    sleep_data = health_log.get("sleep_data", {})
    sleep_hours = health_log.get("sleep_hours_estimated", 0)

    if sleep_hours and sleep_hours < sleep_std.get("recommended_duration_hours", {}).get(
        "adults_18_64", {}
    ).get("min", 7):
        deficit = round(7 - sleep_hours, 1)
        alerts.append(
            {
                "type": "sleep_short",
                "severity": "warning",
                "message": f"昨晚只睡了 {sleep_hours} 小时，低于建议的 7 小时。建议今晚提前 {deficit} 小时上床。",
                "value": sleep_hours,
            }
        )
        burnout_signals += 1

    efficiency = sleep_data.get("efficiency_pct")
    if efficiency and efficiency < sleep_std.get("efficiency_pct", {}).get("good", 85):
        alerts.append(
            {
                "type": "sleep_poor_efficiency",
                "severity": "warning" if efficiency >= 75 else "critical",
                "message": f"睡眠效率 {efficiency}%（建议 ≥85%），入睡困难或中途醒来太多。",
                "value": efficiency,
            }
        )
        if efficiency < 80:
            burnout_signals += 1

    deep_pct = sleep_data.get("deep_pct")
    if deep_pct is not None and deep_pct < sleep_std.get("architecture_pct", {}).get(
        "N3_deep", {}
    ).get("concerning_below", 10):
        alerts.append(
            {
                "type": "deep_sleep_low",
                "severity": "warning",
                "message": f"深度睡眠只有 {deep_pct}%（建议 ≥10%），身体恢复不充分。",
                "value": deep_pct,
            }
        )
        burnout_signals += 1

    latency = sleep_data.get("latency_min")
    if latency and latency > sleep_std.get("latency_minutes", {}).get(
        "insomnia_threshold", 30
    ):
        alerts.append(
            {
                "type": "sleep_latency_high",
                "severity": "warning",
                "message": f"入睡时间 {int(latency)} 分钟（建议 10-20 分钟），可能存在入睡困难。",
                "value": latency,
            }
        )
        burnout_signals += 1

    # --- 心率分析 ---
    resting_hr = health_log.get("resting_hr_bpm")
    if resting_hr:
        if resting_hr > hr_std.get("resting_bpm", {}).get("tachycardia_above", 100):
            alerts.append(
                {
                    "type": "hr_tachycardia",
                    "severity": "critical",
                    "message": f"静息心率 {resting_hr} bpm，超过 100 bpm（心动过速）。如持续请就医。",
                    "value": resting_hr,
                }
            )
        # 检查是否高于基线 5bpm
        hr_history = health_log.get("heart_rate_history", [])
        if hr_history:
            avg_values = [h["avg"] for h in hr_history if h.get("avg")]
            if avg_values:
                avg_7d = sum(avg_values) / len(avg_values)
                delta = resting_hr - avg_7d
                threshold = burnout_std.get("signals", {}).get(
                    "resting_hr_elevated_above_baseline_bpm", 5
                )
                if delta > threshold:
                    alerts.append(
                        {
                            "type": "hr_elevated",
                            "severity": "warning",
                            "message": f"静息心率 {resting_hr} bpm，比近期均值高 {delta:.0f} bpm。可能是压力或缺觉。",
                            "value": resting_hr,
                        }
                    )
                    burnout_signals += 1

    # --- HRV 分析 ---
    hrv_latest = health_log.get("hrv_latest_ms")
    hrv_7day_avg = health_log.get("hrv_7day_avg_ms")

    if hrv_latest is not None:
        # 绝对值检查
        if hrv_latest < hrv_std.get("alerts", {}).get("critically_low_absolute_ms", 15):
            alerts.append(
                {
                    "type": "hrv_critically_low",
                    "severity": "critical",
                    "message": f"HRV 仅 {hrv_latest} ms，严重偏低。身体恢复状态极差，请务必休息。",
                    "value": hrv_latest,
                }
            )
            burnout_signals += 1

        # 与 7 日均值比较
        if hrv_7day_avg and hrv_7day_avg > 0:
            drop_pct = round((1 - hrv_latest / hrv_7day_avg) * 100, 1)
            warning_pct = hrv_std.get("alerts", {}).get("acute_drop_warning_pct", 20)
            if drop_pct > warning_pct:
                alerts.append(
                    {
                        "type": "hrv_acute_drop",
                        "severity": "warning",
                        "message": f"HRV 下降了 {drop_pct}%（7 日均值 {hrv_7day_avg} ms → 今天 {hrv_latest} ms）。恢复状态不佳。",
                        "value": hrv_latest,
                    }
                )
                burnout_signals += 1

        # 按年龄评级
        age_key = _age_to_key(user_age)
        age_norms = hrv_std.get("by_age", {}).get(age_key, {})
        if age_norms and hrv_latest < age_norms.get("low_p25", 0):
            alerts.append(
                {
                    "type": "hrv_below_age_norm",
                    "severity": "info",
                    "message": f"HRV {hrv_latest} ms 低于同龄人 25 百分位（{age_norms['low_p25']} ms）。长期偏低需关注。",
                    "value": hrv_latest,
                }
            )

    # --- 倦怠综合评估 ---
    burnout_threshold = burnout_std.get("alert_threshold_signals", 3)
    severity_map = burnout_std.get("severity", {})

    burnout_result = {"signals_active": burnout_signals, "severity": "normal"}

    if burnout_signals >= 5:
        burnout_result["severity"] = "critical"
        alerts.append(
            {
                "type": "burnout_critical",
                "severity": "critical",
                "message": f"🚨 倦怠风险很高（{burnout_signals}/7 信号）。强烈建议今天停下来休息。",
                "value": burnout_signals,
            }
        )
    elif burnout_signals >= burnout_threshold:
        burnout_result["severity"] = "warning"
        alerts.append(
            {
                "type": "burnout_warning",
                "severity": "warning",
                "message": f"⚠️ 检测到 {burnout_signals} 个倦怠信号同时亮起。建议减量 30%，优先恢复。",
                "value": burnout_signals,
            }
        )
    elif burnout_signals >= 1:
        burnout_result["severity"] = "monitor"

    return {
        "alerts": alerts,
        "burnout": burnout_result,
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
    }


def _age_to_key(age: int) -> str:
    if age <= 25:
        return "18_25"
    elif age <= 35:
        return "26_35"
    elif age <= 45:
        return "36_45"
    elif age <= 55:
        return "46_55"
    elif age <= 65:
        return "56_65"
    else:
        return "65_plus"


# ---------------------------------------------------------------------------
# Flask 接收端（iPhone → EC2）
# ---------------------------------------------------------------------------
def run_server(port: int = 3001):
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        log.error("Flask 未安装，请 pip install flask")
        sys.exit(1)

    app = Flask(__name__)
    WRITE_TOKEN = os.environ["HAE_WRITE_TOKEN"].strip()

    @app.route("/api/health", methods=["POST"])
    def receive_health():
        token = request.headers.get("api-key", "").strip()
        if token != WRITE_TOKEN:
            log.warning("认证失败 — IP: %s", request.remote_addr)
            return jsonify({"error": "Unauthorized"}), 401

        try:
            payload = request.get_json(force=True)
        except Exception as e:
            return jsonify({"error": f"Invalid JSON: {e}"}), 400

        # 保存原始数据
        ensure_dirs()
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        raw_path = HEALTH_RAW_DIR / f"health_raw_{ts}.json"
        save_json(raw_path, payload)
        log.info("原始数据已保存: %s", raw_path)

        # 解析并更新
        record = parse_health_payload(payload)
        health_log = update_health_log(record)

        # 自动分析
        analysis = analyze_health(health_log)

        return jsonify(
            {
                "status": "ok",
                "sleep_hours": health_log.get("sleep_hours_estimated"),
                "resting_hr": health_log.get("resting_hr_bpm"),
                "hrv_latest": health_log.get("hrv_latest_ms"),
                "alerts_count": len(analysis["alerts"]),
                "burnout_severity": analysis["burnout"]["severity"],
            }
        ), 200


    @app.route("/api/health", methods=["GET"])
    def health_dashboard():
        """Dashboard data for health.html frontend."""
        health_log = load_json(HEALTH_LOG_PATH, {})
        daily_history = health_log.get("daily_history", [])
        sd = health_log.get("sleep_data", {})
        history = []
        for d in daily_history[-8:]:
            history.append({
                "date": d.get("date", ""),
                "sleep_hours": d.get("sleep_hours", 0),
                "deep_pct": d.get("deep_pct"),
                "rem_pct": d.get("rem_pct"),
                "efficiency": d.get("efficiency"),
                "resting_hr": d.get("resting_hr"),
                "hrv": d.get("hrv"),
            })
        return jsonify({
            "today": {
                "sleep_hours_estimated": health_log.get("sleep_hours_estimated"),
                "resting_hr_bpm": health_log.get("resting_hr_bpm"),
                "hrv_latest_ms": health_log.get("hrv_latest_ms"),
                "sleep_data": sd,
            },
            "history": history,
        }), 200

    @app.route("/api/meals", methods=["GET"])
    def meals_api():
        """Meal check-in data for health.html frontend."""
        meals_path = Path(HEALTH_LOG_PATH).parent / "meals.json"
        meals_data = load_json(meals_path, {"meals": [], "streak": 0, "calendar": []})
        return jsonify(meals_data), 200

    @app.route("/api/health/status", methods=["GET"])
    def health_status():
        health_log = load_json(HEALTH_LOG_PATH, {})
        return jsonify(
            {
                "last_sync": health_log.get("last_sync"),
                "sleep_hours": health_log.get("sleep_hours_estimated"),
                "resting_hr": health_log.get("resting_hr_bpm"),
                "hrv_latest": health_log.get("hrv_latest_ms"),
            }
        ), 200

    log.info("CareShrimp Health API 启动 — 端口 %d", port)
    app.run(host="0.0.0.0", port=port)


# ---------------------------------------------------------------------------
# Mock 数据生成（测试用）
# ---------------------------------------------------------------------------
def generate_mock_payload() -> dict:
    """生成模拟的 Health Auto Export JSON payload"""
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    return {
        "data": {
            "metrics": [
                {
                    "name": "Sleep Analysis",
                    "units": "min",
                    "data": [
                        {
                            "date": yesterday.strftime("%Y-%m-%d"),
                            "totalSleep": 390,
                            "asleep": 390,
                            "core": 195,
                            "deep": 55,
                            "rem": 85,
                            "sleepStart": f"{yesterday.strftime('%Y-%m-%d')} 23:45:00 +0800",
                            "sleepEnd": f"{now.strftime('%Y-%m-%d')} 06:15:00 +0800",
                            "inBed": 420,
                            "inBedStart": f"{yesterday.strftime('%Y-%m-%d')} 23:20:00 +0800",
                            "inBedEnd": f"{now.strftime('%Y-%m-%d')} 06:20:00 +0800",
                        }
                    ],
                },
                {
                    "name": "Heart Rate",
                    "units": "bpm",
                    "data": [
                        {"date": f"{now.strftime('%Y-%m-%d')} 08:00:00 +0800", "Min": 58, "Avg": 72, "Max": 95},
                        {"date": f"{now.strftime('%Y-%m-%d')} 10:00:00 +0800", "Min": 62, "Avg": 78, "Max": 110},
                        {"date": f"{now.strftime('%Y-%m-%d')} 12:00:00 +0800", "Min": 60, "Avg": 75, "Max": 102},
                        {"date": f"{now.strftime('%Y-%m-%d')} 14:00:00 +0800", "Min": 59, "Avg": 74, "Max": 98},
                    ],
                },
                {
                    "name": "Resting Heart Rate",
                    "units": "bpm",
                    "data": [
                        {"date": now.strftime("%Y-%m-%d"), "Avg": 68},
                    ],
                },
                {
                    "name": "Heart Rate Variability",
                    "units": "ms",
                    "data": [
                        {"date": f"{now.strftime('%Y-%m-%d')} 03:00:00 +0800", "Avg": 42},
                        {"date": f"{now.strftime('%Y-%m-%d')} 04:00:00 +0800", "Avg": 45},
                        {"date": f"{now.strftime('%Y-%m-%d')} 05:00:00 +0800", "Avg": 38},
                    ],
                },
            ],
            "heartRateNotifications": [],
        }
    }


def generate_mock_burnout_payload() -> dict:
    """生成模拟倦怠状态的 payload（测试告警）"""
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    return {
        "data": {
            "metrics": [
                {
                    "name": "Sleep Analysis",
                    "units": "min",
                    "data": [
                        {
                            "date": yesterday.strftime("%Y-%m-%d"),
                            "totalSleep": 280,  # 4.7 小时
                            "asleep": 280,
                            "core": 180,
                            "deep": 20,       # 深度睡眠极低
                            "rem": 35,
                            "sleepStart": f"{yesterday.strftime('%Y-%m-%d')} 02:15:00 +0800",
                            "sleepEnd": f"{now.strftime('%Y-%m-%d')} 06:55:00 +0800",
                            "inBed": 360,
                            "inBedStart": f"{yesterday.strftime('%Y-%m-%d')} 01:30:00 +0800",
                            "inBedEnd": f"{now.strftime('%Y-%m-%d')} 07:30:00 +0800",
                        }
                    ],
                },
                {
                    "name": "Heart Rate",
                    "units": "bpm",
                    "data": [
                        {"date": f"{now.strftime('%Y-%m-%d')} 08:00:00 +0800", "Min": 72, "Avg": 88, "Max": 120},
                        {"date": f"{now.strftime('%Y-%m-%d')} 10:00:00 +0800", "Min": 75, "Avg": 92, "Max": 125},
                    ],
                },
                {
                    "name": "Resting Heart Rate",
                    "units": "bpm",
                    "data": [{"date": now.strftime("%Y-%m-%d"), "Avg": 88}],
                },
                {
                    "name": "Heart Rate Variability",
                    "units": "ms",
                    "data": [
                        {"date": f"{now.strftime('%Y-%m-%d')} 03:00:00 +0800", "Avg": 18},
                        {"date": f"{now.strftime('%Y-%m-%d')} 04:00:00 +0800", "Avg": 15},
                    ],
                },
            ],
            "heartRateNotifications": [],
        }
    }


# ---------------------------------------------------------------------------
# 提醒管理（静音/调频）
# ---------------------------------------------------------------------------
MUTE_CONFIG_PATH = HEALTH_LOG_DIR / "reminder_config.json"

DEFAULT_REMINDER_CONFIG = {
    "muted_until": None,            # ISO timestamp or null
    "reminder_interval_hours": 2,   # 默认每 2 小时
    "night_quiet_start": "23:00",   # 夜间静音开始
    "night_quiet_end": "07:00",     # 夜间静音结束
    "enabled_types": [
        "sleep", "heart_rate", "hrv", "burnout", "work_duration",
        "water", "meal", "weather"
    ],
}


def load_reminder_config() -> dict:
    config = load_json(MUTE_CONFIG_PATH, DEFAULT_REMINDER_CONFIG)
    # 确保所有默认字段存在
    for k, v in DEFAULT_REMINDER_CONFIG.items():
        if k not in config:
            config[k] = v
    return config


def save_reminder_config(config: dict):
    ensure_dirs()
    save_json(MUTE_CONFIG_PATH, config)


def handle_mute_command(command: str) -> str:
    """
    处理 Telegram 静音指令
    支持格式：
      静音2h / 静音30m / mute 2h / mute 30m
      取消静音 / unmute
      提醒频率4h / interval 4h
      夜间静音 23:00-07:00
      关闭睡眠提醒 / 开启心率提醒
    """
    config = load_reminder_config()
    cmd = command.strip().lower()

    # 静音 N 小时/分钟
    import re
    mute_match = re.match(r"(?:静音|mute)\s*(\d+)\s*(h|m|小时|分钟)", cmd)
    if mute_match:
        amount = int(mute_match.group(1))
        unit = mute_match.group(2)
        if unit in ("m", "分钟"):
            delta = timedelta(minutes=amount)
            desc = f"{amount} 分钟"
        else:
            delta = timedelta(hours=amount)
            desc = f"{amount} 小时"
        until = datetime.utcnow() + delta
        config["muted_until"] = until.isoformat() + "Z"
        save_reminder_config(config)
        return f"🔇 已静音 {desc}，到 {until.strftime('%H:%M')} UTC 自动恢复提醒。"

    # 取消静音
    if cmd in ("取消静音", "unmute", "恢复提醒"):
        config["muted_until"] = None
        save_reminder_config(config)
        return "🔔 已恢复提醒。"

    # 调整提醒频率
    interval_match = re.match(r"(?:提醒频率|interval)\s*(\d+)\s*(h|小时)", cmd)
    if interval_match:
        hours = int(interval_match.group(1))
        if hours < 1:
            hours = 1
        if hours > 12:
            hours = 12
        config["reminder_interval_hours"] = hours
        save_reminder_config(config)
        return f"⏱ 提醒频率已调整为每 {hours} 小时一次。"

    # 夜间静音时段
    night_match = re.match(r"(?:夜间静音|night\s*quiet)\s*(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})", cmd)
    if night_match:
        config["night_quiet_start"] = night_match.group(1)
        config["night_quiet_end"] = night_match.group(2)
        save_reminder_config(config)
        return f"🌙 夜间静音已设为 {config['night_quiet_start']} - {config['night_quiet_end']}。"

    # 开关特定类型提醒
    toggle_off = re.match(r"(?:关闭|disable)\s*(.+?)(?:提醒)?$", cmd)
    if toggle_off:
        rtype = _map_reminder_type(toggle_off.group(1))
        if rtype and rtype in config["enabled_types"]:
            config["enabled_types"].remove(rtype)
            save_reminder_config(config)
            return f"已关闭「{rtype}」类型提醒。"
        return f"未找到「{toggle_off.group(1)}」类型提醒。可选: {', '.join(DEFAULT_REMINDER_CONFIG['enabled_types'])}"

    toggle_on = re.match(r"(?:开启|enable)\s*(.+?)(?:提醒)?$", cmd)
    if toggle_on:
        rtype = _map_reminder_type(toggle_on.group(1))
        if rtype and rtype not in config["enabled_types"]:
            config["enabled_types"].append(rtype)
            save_reminder_config(config)
            return f"已开启「{rtype}」类型提醒。"
        return f"「{rtype}」提醒已处于开启状态。"

    # 查看当前配置
    if cmd in ("提醒状态", "reminder status", "提醒设置"):
        muted = config.get("muted_until")
        if muted:
            muted_str = f"静音到 {muted}"
        else:
            muted_str = "未静音"
        return (
            f"📋 提醒配置：\n"
            f"  状态: {muted_str}\n"
            f"  频率: 每 {config['reminder_interval_hours']} 小时\n"
            f"  夜间静音: {config['night_quiet_start']} - {config['night_quiet_end']}\n"
            f"  已开启: {', '.join(config['enabled_types'])}"
        )

    return "未识别的指令。支持: 静音2h / 取消静音 / 提醒频率4h / 夜间静音 23:00-07:00 / 关闭睡眠提醒 / 提醒状态"


def _map_reminder_type(text: str) -> str:
    mapping = {
        "睡眠": "sleep", "sleep": "sleep",
        "心率": "heart_rate", "hr": "heart_rate", "heart": "heart_rate",
        "hrv": "hrv",
        "倦怠": "burnout", "burnout": "burnout",
        "工作": "work_duration", "work": "work_duration",
        "饮水": "water", "水": "water", "water": "water",
        "饮食": "meal", "吃饭": "meal", "meal": "meal",
        "天气": "weather", "weather": "weather",
    }
    return mapping.get(text.strip().lower(), text.strip().lower())


def is_reminder_allowed(alert_type: str = "general") -> bool:
    """检查当前是否允许发送提醒"""
    config = load_reminder_config()

    # 检查静音
    muted_until = config.get("muted_until")
    if muted_until:
        try:
            mute_end = datetime.fromisoformat(muted_until.replace("Z", "+00:00"))
            if datetime.utcnow().replace(tzinfo=mute_end.tzinfo) < mute_end:
                return False
        except (ValueError, TypeError):
            pass

    # 检查夜间静音（但 critical 告警不受限）
    if alert_type != "burnout_critical":
        now_str = datetime.utcnow().strftime("%H:%M")
        quiet_start = config.get("night_quiet_start", "23:00")
        quiet_end = config.get("night_quiet_end", "07:00")
        if quiet_start > quiet_end:  # 跨午夜
            if now_str >= quiet_start or now_str < quiet_end:
                return False
        elif quiet_start <= now_str < quiet_end:
            return False

    # 检查类型是否启用
    mapped_type = alert_type.split("_")[0] if "_" in alert_type else alert_type
    enabled = config.get("enabled_types", DEFAULT_REMINDER_CONFIG["enabled_types"])
    if mapped_type not in enabled and alert_type not in ("burnout_critical", "burnout_warning"):
        return False

    return True


# ---------------------------------------------------------------------------
# Telegram 通知（可选）
# ---------------------------------------------------------------------------
def send_tg_alert(message: str):
    """发送 Telegram 告警（如配置了环境变量）"""
    token = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("TG 未配置，跳过通知: %s", message[:50])
        return

    import urllib.request
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    ).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
        log.info("TG 通知已发送")
    except Exception as e:
        log.warning("TG 通知失败: %s", e)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="CareShrimp Apple Health Sync")
    parser.add_argument("--serve", action="store_true", help="启动 Flask 接收端")
    parser.add_argument("--port", type=int, default=3001, help="Flask 端口 (默认 3001)")
    parser.add_argument("--mock", action="store_true", help="用 mock 数据测试")
    parser.add_argument("--mock-burnout", action="store_true", help="用倦怠 mock 数据测试")
    parser.add_argument("--analyze", action="store_true", help="分析当前 health_log.json")
    parser.add_argument("--age", type=int, default=30, help="用户年龄 (默认 30)")
    parser.add_argument("--mute", type=str, help="静音指令，如 '静音2h'")
    args = parser.parse_args()

    ensure_dirs()

    if args.serve:
        run_server(port=args.port)
    elif args.mock or args.mock_burnout:
        payload = generate_mock_burnout_payload() if args.mock_burnout else generate_mock_payload()
        log.info("使用 %s mock 数据...", "倦怠" if args.mock_burnout else "正常")
        record = parse_health_payload(payload)
        health_log = update_health_log(record)
        analysis = analyze_health(health_log, user_age=args.age)
        print("\n" + "=" * 60)
        print("📊 健康数据摘要")
        print("=" * 60)
        print(f"  睡眠: {health_log.get('sleep_hours_estimated', 'N/A')} 小时")
        sd = health_log.get("sleep_data", {})
        if sd:
            print(f"  效率: {sd.get('efficiency_pct', 'N/A')}%")
            print(f"  深度睡眠: {sd.get('deep_pct', 'N/A')}%")
            print(f"  REM: {sd.get('rem_pct', 'N/A')}%")
            if sd.get("latency_min") is not None:
                print(f"  入睡时间: {sd['latency_min']:.0f} 分钟")
        print(f"  静息心率: {health_log.get('resting_hr_bpm', 'N/A')} bpm")
        print(f"  HRV: {health_log.get('hrv_latest_ms', 'N/A')} ms")
        print()
        if analysis["alerts"]:
            print("⚠️ 告警:")
            for a in analysis["alerts"]:
                icon = "🚨" if a["severity"] == "critical" else "⚠️" if a["severity"] == "warning" else "ℹ️"
                print(f"  {icon} [{a['type']}] {a['message']}")
        else:
            print("✅ 所有指标正常")
        print(f"\n倦怠评估: {analysis['burnout']['severity']} ({analysis['burnout']['signals_active']}/7 信号)")
        print("=" * 60)
    elif args.analyze:
        health_log = load_json(HEALTH_LOG_PATH, {})
        if not health_log:
            log.error("health_log.json 为空，请先同步数据 (--mock 或 --serve)")
            sys.exit(1)
        analysis = analyze_health(health_log, user_age=args.age)
        print(json.dumps(analysis, indent=2, ensure_ascii=False))
    elif args.mute:
        result = handle_mute_command(args.mute)
        print(result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
