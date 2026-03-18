#!/usr/bin/env python3
"""
publisher/wechat_draft.py — 微信公众号草稿箱发布
使用微信公众号 API: 获取 access_token → 上传图片 → 新增草稿

API 文档: https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html

需要环境变量:
  WECHAT_APP_ID — 公众号 AppID
  WECHAT_APP_SECRET — 公众号 AppSecret

铁律 #1: 所有 os.environ 必须 .strip()
"""
import os
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hotspot.config import log

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

WECHAT_APP_ID = os.environ.get("WECHAT_APP_ID", "").strip()
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "").strip()

_access_token_cache = {"token": "", "expires": 0}


def get_access_token() -> str:
    """Get WeChat access_token (cached for 2 hours)."""
    import time
    if _access_token_cache["token"] and _access_token_cache["expires"] > time.time():
        return _access_token_cache["token"]

    if not WECHAT_APP_ID or not WECHAT_APP_SECRET:
        log.error("WECHAT_APP_ID or WECHAT_APP_SECRET not set")
        return ""

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": WECHAT_APP_ID,
                    "secret": WECHAT_APP_SECRET,
                },
            )
            data = resp.json()
            token = data.get("access_token", "")
            if token:
                _access_token_cache["token"] = token
                _access_token_cache["expires"] = time.time() + 7000  # ~2hr
                log.info("WeChat access_token obtained")
                return token
            log.error(f"WeChat token error: {data}")
            return ""
    except Exception as e:
        log.error(f"WeChat token request failed: {e}")
        return ""


def upload_image(image_path: str) -> str:
    """
    Upload image as permanent material.
    Returns media_id for use in articles.
    """
    token = get_access_token()
    if not token:
        return ""

    try:
        with httpx.Client(timeout=30) as client:
            with open(image_path, "rb") as f:
                resp = client.post(
                    f"https://api.weixin.qq.com/cgi-bin/material/add_material"
                    f"?access_token={token}&type=image",
                    files={"media": (os.path.basename(image_path), f, "image/png")},
                )
            data = resp.json()
            media_id = data.get("media_id", "")
            if media_id:
                log.info(f"Uploaded image: {media_id}")
                return media_id
            log.error(f"Image upload failed: {data}")
            return ""
    except Exception as e:
        log.error(f"Image upload error: {e}")
        return ""


def upload_article_image(image_path: str) -> str:
    """
    Upload image for use inside article body (different endpoint).
    Returns URL to embed in article HTML.
    """
    token = get_access_token()
    if not token:
        return ""

    try:
        with httpx.Client(timeout=30) as client:
            with open(image_path, "rb") as f:
                resp = client.post(
                    f"https://api.weixin.qq.com/cgi-bin/media/uploadimg"
                    f"?access_token={token}",
                    files={"media": (os.path.basename(image_path), f, "image/png")},
                )
            data = resp.json()
            url = data.get("url", "")
            if url:
                log.info(f"Uploaded article image: {url[:50]}...")
                return url
            log.error(f"Article image upload failed: {data}")
            return ""
    except Exception as e:
        log.error(f"Article image upload error: {e}")
        return ""


def add_draft(title: str, content_html: str, thumb_media_id: str,
              author: str = "Ian's OPC", digest: str = "") -> dict:
    """
    Add article to WeChat draft box.

    Args:
        title: Article title
        content_html: Article HTML body
        thumb_media_id: Cover image media_id (from upload_image)
        author: Author name
        digest: Article summary (optional, auto-generated if empty)

    Returns: {"media_id": "...", "success": True/False}
    """
    token = get_access_token()
    if not token:
        return {"success": False, "error": "No access_token"}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}",
                json={
                    "articles": [{
                        "title": title[:64],
                        "author": author,
                        "digest": digest[:120] if digest else "",
                        "content": content_html,
                        "thumb_media_id": thumb_media_id,
                        "need_open_comment": 1,
                        "only_fans_can_comment": 0,
                    }],
                },
            )
            data = resp.json()
            if data.get("media_id"):
                log.info(f"Draft added: {data['media_id']}")
                return {"success": True, "media_id": data["media_id"]}
            log.error(f"Draft add failed: {data}")
            return {"success": False, "error": str(data)}
    except Exception as e:
        log.error(f"Draft add error: {e}")
        return {"success": False, "error": str(e)}


def publish_to_draft(title: str, content_html: str,
                     cover_image_path: str = None,
                     article_images: list[str] = None) -> dict:
    """
    High-level function: upload images + add draft.
    Returns {success, media_id, message}
    """
    # Upload cover image
    thumb_media_id = ""
    if cover_image_path and os.path.exists(cover_image_path):
        thumb_media_id = upload_image(cover_image_path)

    if not thumb_media_id:
        log.warning("No cover image, using placeholder")
        # WeChat requires thumb_media_id, create a minimal draft
        return {"success": False, "error": "Cover image required for WeChat draft"}

    # Upload and replace article body images
    if article_images:
        for img_path in article_images:
            if os.path.exists(img_path):
                img_url = upload_article_image(img_path)
                if img_url:
                    # Replace local path reference with WeChat URL in HTML
                    img_name = os.path.basename(img_path)
                    content_html = content_html.replace(img_name, img_url)

    result = add_draft(title, content_html, thumb_media_id)
    if result["success"]:
        return {
            "success": True,
            "media_id": result["media_id"],
            "message": f"微信草稿已保存 (media_id: {result['media_id']})",
        }
    return result
