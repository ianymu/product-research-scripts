#!/usr/bin/env python3
"""
publisher/xhs_draft.py — 小红书草稿箱方案

XHS 无官方 API。草稿箱发布方案:
  方案 A (推荐): 内容存 Supabase draft_contents → 手机预览页 → 用户手动复制粘贴到 XHS APP
  方案 B (高风险): Playwright 自动化登录 XHS → 创建草稿 → 保存 (容易被封号)

当前实现: 方案 A
  - 生成好的内容已存在 draft_contents (platform='xiaohongshu')
  - 提供手机端预览 URL
  - 用户通过预览页一键复制文案 + 下载图片
  - 手动粘贴到 XHS APP 发布

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import sb_query, sb_insert, log

EC2_HOST = os.environ.get("EC2_HOST", "18.221.160.170").strip()


def get_xhs_drafts(status: str = "draft", limit: int = 10) -> list[dict]:
    """Get XHS drafts from Supabase."""
    path = (
        f"draft_contents?select=id,title,content,images,hotspot_topic,created_at"
        f"&platform=eq.xiaohongshu&status=eq.{status}"
        f"&order=created_at.desc&limit={limit}"
    )
    return sb_query(path)


def get_preview_url(draft_id: str) -> str:
    """Get mobile preview URL for a draft."""
    return f"http://{EC2_HOST}/preview/xhs/{draft_id}"


def prepare_for_manual_publish(draft_id: str) -> dict:
    """
    Prepare XHS content for manual publishing.
    Returns {title, content, images, preview_url, copy_instructions}
    """
    drafts = sb_query(
        f"draft_contents?select=*&id=eq.{draft_id}&limit=1"
    )
    if not drafts:
        return {"error": f"Draft {draft_id} not found"}

    draft = drafts[0]
    preview_url = get_preview_url(draft_id)

    return {
        "title": draft.get("title", ""),
        "content": draft.get("content", ""),
        "images": draft.get("images", []),
        "preview_url": preview_url,
        "copy_instructions": (
            f"小红书发布步骤:\n"
            f"1. 打开预览链接: {preview_url}\n"
            f"2. 长按文案区域复制全部文字\n"
            f"3. 长按每张图片保存到手机相册\n"
            f"4. 打开小红书 APP → 点 '+' → 选择图片\n"
            f"5. 粘贴文案 → 添加话题标签 → 发布\n"
        ),
    }


def format_tg_notification(draft_id: str, title: str) -> str:
    """Format TG notification for XHS draft."""
    preview_url = get_preview_url(draft_id)
    return (
        f"📕 *小红书内容就绪*\n\n"
        f"标题: {title}\n"
        f"预览: {preview_url}\n\n"
        f"⚠️ XHS 不支持自动发布，请手动:\n"
        f"1. 打开预览链接复制文案+保存图片\n"
        f"2. 打开小红书 APP 发布"
    )
