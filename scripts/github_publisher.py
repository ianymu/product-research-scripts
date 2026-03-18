#!/usr/bin/env python3
"""
GitHub Publisher — 专业 README + Landing Page + 浏览追踪

将 MVP 产品打包发布到 GitHub：
  1. 生成专业 README（截图+GIF+安装指南+Badge）
  2. 生成 Landing Page（GitHub Pages ready）
  3. 配置浏览追踪（hits counter badge）
  4. 创建 GitHub Release

Usage:
  python3 github_publisher.py --repo "ianymu/my-product" --name "ProductName" --desc "one liner"
  python3 github_publisher.py --local ./my-product --prepare  # 只生成文件，不推送

Env: ANTHROPIC_API_KEY, GITHUB_TOKEN (optional for API ops)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from anthropic import Anthropic

# ── Config ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("github-publisher")

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── README Generation ──────────────────────────────────────────────────────

def generate_readme(
    product_name: str,
    one_liner: str,
    features: list[str],
    tech_stack: str = "",
    repo_name: str = "",
    screenshots: list[str] | None = None,
) -> str:
    """Generate a professional README.md."""
    log.info("Generating README for %s...", product_name)

    prompt = f"""Generate a professional GitHub README.md for this product:

Product: {product_name}
One-liner: {one_liner}
Features: {json.dumps(features)}
Tech Stack: {tech_stack or "Not specified"}
Repo: {repo_name or "owner/repo"}

Requirements:
1. Start with a centered logo area (use emoji as placeholder) + product name + badges
2. Badges: GitHub stars, license (MIT), hits counter (https://hits.seeyoufarm.com)
3. Hero section with a clean screenshot placeholder: `![Demo](./docs/demo.png)`
4. Features section with emoji icons, 2-column layout using HTML table
5. Quick Start section (install + run in 3 commands max)
6. Usage section with code examples
7. Configuration section (env vars table)
8. Architecture section (mermaid diagram)
9. Contributing section (brief)
10. License section (MIT)
11. Built with section (tech stack badges from shields.io)

Style:
- Professional indie hacker style (like Raycast, Cal.com, Dub.co READMEs)
- Not too long — scannable in 60 seconds
- Code blocks with syntax highlighting
- Mermaid diagram for architecture

Output pure Markdown, no wrapping."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── Landing Page Generation ────────────────────────────────────────────────

def generate_landing_page(
    product_name: str,
    one_liner: str,
    features: list[str],
    repo_url: str = "",
) -> str:
    """Generate a single-page landing page for GitHub Pages."""
    log.info("Generating Landing Page for %s...", product_name)

    prompt = f"""Generate a single-file HTML landing page for this product:

Product: {product_name}
One-liner: {one_liner}
Features: {json.dumps(features)}
GitHub: {repo_url or "#"}

Requirements:
1. Single HTML file with inline Tailwind CSS (CDN)
2. Dark theme, modern design (like linear.app / raycast.com aesthetic)
3. Sections:
   - Hero: Product name + one-liner + CTA buttons (GitHub + Try Demo)
   - Features: 3-column grid with icons (use emoji)
   - How It Works: 3-step visual flow
   - Tech Stack: Badge pills
   - Footer: GitHub link + "Built by a solo founder"
4. Smooth scroll navigation
5. Responsive (mobile-first)
6. Subtle animations (fade-in on scroll using Intersection Observer)
7. No external dependencies except Tailwind CDN
8. SEO meta tags (title, description, og:image placeholder)
9. Analytics: Include a hits counter pixel from hits.seeyoufarm.com

Color scheme: Dark bg (#0a0a0a), accent color auto-chosen based on product type.

Output pure HTML, no wrapping."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── File Writer ─────────────────────────────────────────────────────────────

def prepare_repo_files(
    output_dir: str,
    product_name: str,
    one_liner: str,
    features: list[str],
    tech_stack: str = "",
    repo_name: str = "",
) -> dict:
    """Generate all publishable files."""
    os.makedirs(output_dir, exist_ok=True)
    docs_dir = os.path.join(output_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)

    results = {}

    # 1. README.md
    readme = generate_readme(product_name, one_liner, features, tech_stack, repo_name)
    readme_path = os.path.join(output_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    results["readme"] = readme_path

    # 2. Landing Page
    lp = generate_landing_page(
        product_name, one_liner, features,
        f"https://github.com/{repo_name}" if repo_name else "#",
    )
    lp_path = os.path.join(docs_dir, "index.html")
    with open(lp_path, "w", encoding="utf-8") as f:
        f.write(lp)
    results["landing_page"] = lp_path

    # 3. LICENSE (MIT)
    license_text = f"""MIT License

Copyright (c) {datetime.now().year} Ian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
    license_path = os.path.join(output_dir, "LICENSE")
    with open(license_path, "w") as f:
        f.write(license_text)
    results["license"] = license_path

    # 4. .github/FUNDING.yml
    github_dir = os.path.join(output_dir, ".github")
    os.makedirs(github_dir, exist_ok=True)
    funding_path = os.path.join(github_dir, "FUNDING.yml")
    with open(funding_path, "w") as f:
        f.write("github: [ianymu]\n")
    results["funding"] = funding_path

    log.info("Prepared %d files in %s", len(results), output_dir)
    return results


# ── GitHub API ──────────────────────────────────────────────────────────────

def gh_api(method: str, path: str, data: dict | None = None) -> dict:
    """Call GitHub API."""
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN, skipping API call: %s %s", method, path)
        return {}
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com{path}"
    try:
        if method == "GET":
            resp = httpx.get(url, headers=headers, timeout=15)
        elif method == "POST":
            resp = httpx.post(url, headers=headers, json=data, timeout=15)
        elif method == "PUT":
            resp = httpx.put(url, headers=headers, json=data, timeout=15)
        else:
            return {}
        return resp.json() if resp.status_code < 400 else {"error": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        return {"error": str(e)}


def enable_github_pages(repo_name: str) -> dict:
    """Enable GitHub Pages on docs/ folder."""
    return gh_api("POST", f"/repos/{repo_name}/pages", {
        "source": {"branch": "main", "path": "/docs"},
    })


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GitHub Publisher")
    parser.add_argument("--repo", type=str, help="GitHub repo (owner/name)")
    parser.add_argument("--name", type=str, required=True, help="Product name")
    parser.add_argument("--desc", type=str, required=True, help="One-liner description")
    parser.add_argument("--features", type=str, nargs="+", default=[], help="Feature list")
    parser.add_argument("--tech", type=str, default="", help="Tech stack")
    parser.add_argument("--local", type=str, default="./publish-output", help="Local output dir")
    parser.add_argument("--prepare", action="store_true", help="Only generate files, don't push")
    parser.add_argument("--enable-pages", action="store_true", help="Enable GitHub Pages")
    args = parser.parse_args()

    results = prepare_repo_files(
        output_dir=args.local,
        product_name=args.name,
        one_liner=args.desc,
        features=args.features or [args.desc],
        tech_stack=args.tech,
        repo_name=args.repo or "",
    )

    for name, path in results.items():
        print(f"  {name}: {path}")

    if args.enable_pages and args.repo:
        log.info("Enabling GitHub Pages...")
        result = enable_github_pages(args.repo)
        if "error" not in result:
            print(f"  Pages URL: https://{args.repo.split('/')[0]}.github.io/{args.repo.split('/')[1]}/")
        else:
            print(f"  Pages error: {result}")


if __name__ == "__main__":
    main()
