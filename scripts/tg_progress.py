#!/usr/bin/env python3
"""
tg_progress.py — TG 长任务步骤通知
用法:
    progress = TGProgress("热点监测", 6)
    progress.step("WeChat 采集")
    ...
    progress.finish("http://18.221.160.170/hotspot-monitor.html")
"""
import os
import logging

try:
    import httpx
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

log = logging.getLogger("tg_progress")

TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()


def _send_tg(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("TG credentials not set, printing instead")
        print(text)
        return
    try:
        with httpx.Client(timeout=10) as client:
            client.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
    except Exception as e:
        log.error(f"TG send error: {e}")
        print(text)


class TGProgress:
    def __init__(self, task_name: str, total_steps: int):
        self.task_name = task_name
        self.total = total_steps
        self.current = 0
        _send_tg(f"🚀 *{task_name}* 开始 (共 {total_steps} 步)")

    def step(self, step_name: str):
        self.current += 1
        _send_tg(f"✅ [{self.current}/{self.total}] {step_name}")

    def finish(self, result_url: str = ""):
        url_part = f"\n🔗 {result_url}" if result_url else ""
        _send_tg(f"🎉 *{self.task_name}* 完成!{url_part}")

    def fail(self, error: str):
        _send_tg(f"❌ *{self.task_name}* 失败: {error}")
