"""
pain_aggregator TG 输出补丁

修改点:
1. LLM prompt 加全中文指令
2. TG 只推 Top 5（不是全部）
3. 末尾加网站链接

用法: 将这两个函数替换 pain_aggregator.py 中对应函数
"""

WEB_URL = "http://18.221.160.170/shrimp"


# ── 中文 prompt 补丁 ──
CHINESE_INSTRUCTION = """
重要：所有输出字段必须使用中文，包括但不限于：
- name（产品概念名称）
- one_liner（一句话定位）
- target_user（目标用户）
- problem_statement（解决什么问题）
- value_proposition（价值主张）
- mvp_scope（MVP 范围）
- reasoning（聚合理由）
- go_or_kill_recommendation 的理由部分
唯一例外：competitors 中的产品名可以保留英文原名。
"""


def push_directions_to_tg_v2(directions: list, all_directions: list = None):
    """
    TG 只推 Top 5 方向（精简版），完整列表看网站。

    Args:
        directions: GO 候选方向（高分）
        all_directions: 全部方向（用于统计）
    """
    import os
    import time
    import httpx

    TG_TOKEN = os.environ.get("TG_SHRIMPILOT_TOKEN", "").strip()
    TG_CHAT_ID = os.environ.get("TG_SHRIMPILOT_CHAT_ID", "").strip()

    if not TG_TOKEN or not TG_CHAT_ID:
        return

    total_count = len(all_directions) if all_directions else len(directions)

    # 排序取 Top 5
    sorted_dirs = sorted(directions, key=lambda d: d.get("weighted_score", 0), reverse=True)
    top5 = sorted_dirs[:5]

    if not top5:
        text = "🔍 *产研虾*: 本轮无高分应用方向"
        _tg_send(TG_TOKEN, TG_CHAT_ID, text)
        return

    lines = [
        f"🎯 *产研虾 — Top 5 高分方向*",
        "",
    ]

    for i, d in enumerate(top5):
        score = d.get("weighted_score", 0)
        name = d.get("name", "未命名")
        one_liner = d.get("one_liner", "")
        target = d.get("target_user", "")
        rec = d.get("go_or_kill_recommendation", "")

        # 推荐标记
        if "GO" in rec.upper():
            rec_emoji = "🟢"
        elif "MAYBE" in rec.upper():
            rec_emoji = "🟡"
        else:
            rec_emoji = "🔴"

        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        lines.append(f"{num_emojis[i]} *{name}* (⚡{score:.0f}分) {rec_emoji}")
        if one_liner:
            lines.append(f"   {one_liner}")
        if target:
            lines.append(f"   👤 {target}")
        lines.append("")

    lines.append(f"🔗 全部 {total_count} 个方向详情 → {WEB_URL}/research")
    lines.append("")
    lines.append("回复 `GO 1,2` 确认 | `KILL 3` 放弃")

    text = "\n".join(lines)

    if len(text) <= 4096:
        _tg_send(TG_TOKEN, TG_CHAT_ID, text)
    else:
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            _tg_send(TG_TOKEN, TG_CHAT_ID, chunk)
            time.sleep(0.5)


def _tg_send(token: str, chat_id: str, text: str) -> bool:
    import httpx
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False
