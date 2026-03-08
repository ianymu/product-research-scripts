"""
V7 Pipeline — Landing Page Generator
Generates a validation LP with email capture, deploys to Vercel.
"""
import os
import json
import sys
import base64
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"].strip()
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "").strip()


def generate_lp(direction_name: str, value_prop: str, features: list,
                cta_text: str = "Join the Waitlist") -> str:
    """Generate LP HTML with Tailwind CSS and email capture."""
    features_html = "\n".join([
        f"""<div class="p-6 bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition">
            <h3 class="font-semibold text-lg mb-2 text-gray-900">{f["title"]}</h3>
            <p class="text-gray-600 text-sm">{f["desc"]}</p>
        </div>"""
        for f in features
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{direction_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .gradient-bg {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
    </style>
</head>
<body class="bg-gray-50 text-gray-900">
    <!-- Hero -->
    <div class="gradient-bg text-white">
        <div class="max-w-4xl mx-auto px-4 py-20 text-center">
            <h1 class="text-5xl font-bold mb-6">{direction_name}</h1>
            <p class="text-xl opacity-90 max-w-2xl mx-auto mb-8">{value_prop}</p>
            <a href="#signup" class="inline-block px-8 py-4 bg-white text-purple-700 rounded-lg font-bold text-lg hover:bg-gray-100 transition">
                {cta_text} &rarr;
            </a>
        </div>
    </div>

    <!-- Features -->
    <div class="max-w-5xl mx-auto px-4 py-16">
        <h2 class="text-3xl font-bold text-center mb-12">What You Get</h2>
        <div class="grid md:grid-cols-3 gap-6">{features_html}</div>
    </div>

    <!-- Social Proof -->
    <div class="bg-white py-12">
        <div class="max-w-3xl mx-auto px-4 text-center">
            <p class="text-lg text-gray-600 italic">"Building alone is hard. Building together changes everything."</p>
            <p class="mt-2 text-sm text-gray-400">— Based on 200+ solopreneur pain points we analyzed</p>
        </div>
    </div>

    <!-- Signup -->
    <div id="signup" class="max-w-md mx-auto px-4 py-16 text-center">
        <h2 class="text-3xl font-bold mb-4">Get Early Access</h2>
        <p class="text-gray-600 mb-6">Join founders who are tired of building alone.</p>
        <form id="signup-form" class="flex gap-2">
            <input type="email" id="email" placeholder="your@email.com"
                class="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500" required>
            <button type="submit" class="px-6 py-3 bg-purple-600 text-white rounded-lg hover:bg-purple-700 font-semibold transition">
                Join
            </button>
        </form>
        <p id="success" class="mt-4 text-green-600 hidden font-medium">You're in! We'll be in touch soon.</p>
        <p id="error" class="mt-4 text-red-500 hidden">Something went wrong. Try again?</p>
    </div>

    <!-- Footer -->
    <footer class="text-center py-8 text-sm text-gray-400">
        <p>A V7 Pipeline validation experiment</p>
    </footer>

    <script>
        document.getElementById('signup-form').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const email = document.getElementById('email').value;
            const btn = e.target.querySelector('button');
            btn.disabled = true;
            btn.textContent = '...';
            try {{
                const resp = await fetch('{SUPABASE_URL}/rest/v1/lp_signups', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'apikey': '{SUPABASE_ANON_KEY}',
                        'Authorization': 'Bearer {SUPABASE_ANON_KEY}'
                    }},
                    body: JSON.stringify({{
                        email: email,
                        direction: '{direction_name}',
                        source: document.referrer || 'direct',
                        created_at: new Date().toISOString()
                    }})
                }});
                if (!resp.ok) throw new Error(resp.status);
                document.getElementById('success').classList.remove('hidden');
                document.getElementById('signup-form').classList.add('hidden');
            }} catch(err) {{
                console.error(err);
                document.getElementById('error').classList.remove('hidden');
                btn.disabled = false;
                btn.textContent = 'Join';
            }}
        }});
    </script>
</body>
</html>"""


def deploy_to_vercel(html_content: str, project_name: str) -> str:
    """Deploy LP to Vercel via API. Returns deployment URL."""
    if not VERCEL_TOKEN:
        print("VERCEL_TOKEN not set, skipping deployment", file=sys.stderr)
        return ""

    # Encode file content
    encoded = base64.b64encode(html_content.encode()).decode()

    payload = {
        "name": project_name,
        "files": [
            {"file": "index.html", "data": encoded, "encoding": "base64"},
        ],
        "projectSettings": {
            "framework": None,
        },
    }

    resp = requests.post(
        "https://api.vercel.com/v13/deployments",
        headers={
            "Authorization": f"Bearer {VERCEL_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    url = f"https://{data['url']}"
    print(f"Deployed to Vercel: {url}")
    return url


def generate_and_deploy(direction_name: str, value_prop: str, features: list,
                        project_name: str = "v7-lp-validation",
                        deploy: bool = True) -> dict:
    """Generate LP HTML, save locally, optionally deploy to Vercel."""
    html = generate_lp(direction_name, value_prop, features)

    # Save locally
    output_dir = os.path.join(os.path.dirname(__file__), "..", "output", "lp")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "index.html")
    with open(output_path, "w") as f:
        f.write(html)
    print(f"LP saved: {output_path}")

    result = {"local_path": output_path, "url": ""}

    if deploy:
        result["url"] = deploy_to_vercel(html, project_name)

    return result


if __name__ == "__main__":
    # Default: solopreneur community platform
    name = sys.argv[1] if len(sys.argv) > 1 else "Solopreneur OS"
    value_prop = sys.argv[2] if len(sys.argv) > 2 else (
        "The accountability + community + growth platform for indie founders. "
        "Stop building alone — join a tribe that holds you accountable, "
        "shares growth tactics, and celebrates your wins."
    )
    default_features = [
        {"title": "Accountability Partners", "desc": "Get matched with a founder at your stage. Weekly check-ins keep you on track."},
        {"title": "Growth Playbooks", "desc": "Crowdsourced tactics from founders who've done it. No theory, just what works."},
        {"title": "Build in Public", "desc": "Share progress, get feedback, attract your first users from the community."},
        {"title": "Revenue Milestones", "desc": "Track MRR goals together. Celebrate $1K, $10K, $100K with your cohort."},
        {"title": "AI Co-pilot", "desc": "AI-powered suggestions based on what worked for similar products in your niche."},
        {"title": "Founder Matching", "desc": "Find co-founders, advisors, or beta testers. Filtered by stage, niche, and timezone."},
    ]

    if len(sys.argv) > 3:
        features = [{"title": f, "desc": ""} for f in sys.argv[3].split(",")]
    else:
        features = default_features

    result = generate_and_deploy(name, value_prop, features,
                                  deploy=bool(VERCEL_TOKEN))
    print(json.dumps(result, indent=2))
