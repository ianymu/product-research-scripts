"""
V7 Pipeline — Landing Page Generator
Generates a validation LP with email capture, deploys to Vercel.
"""
import os
import json
import sys

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "")


def generate_lp(direction_name: str, value_prop: str, features: list) -> str:
    """Generate LP HTML with Tailwind CSS."""
    features_html = "\n".join([
        f'<div class="p-6 bg-white rounded-lg shadow"><h3 class="font-semibold text-lg mb-2">{f}</h3></div>'
        for f in features
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{direction_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto px-4 py-16">
        <header class="text-center mb-16">
            <h1 class="text-4xl font-bold text-gray-900 mb-4">{direction_name}</h1>
            <p class="text-xl text-gray-600 max-w-2xl mx-auto">{value_prop}</p>
        </header>
        <section class="grid md:grid-cols-3 gap-6 mb-16">{features_html}</section>
        <section class="max-w-md mx-auto text-center">
            <h2 class="text-2xl font-bold mb-4">Get Early Access</h2>
            <form id="signup-form" class="flex gap-2">
                <input type="email" id="email" placeholder="your@email.com"
                    class="flex-1 px-4 py-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                <button type="submit" class="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-semibold">
                    Join Waitlist
                </button>
            </form>
            <p id="success" class="mt-4 text-green-600 hidden">Thanks! You're on the list.</p>
        </section>
    </div>
    <script>
        document.getElementById('signup-form').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const email = document.getElementById('email').value;
            try {{
                await fetch('{SUPABASE_URL}/rest/v1/lp_signups', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json', 'apikey': '{os.environ.get("SUPABASE_ANON_KEY", "")}'}},
                    body: JSON.stringify({{email, direction: '{direction_name}', created_at: new Date().toISOString()}})
                }});
            }} catch(err) {{ console.error(err); }}
            document.getElementById('success').classList.remove('hidden');
            document.getElementById('signup-form').classList.add('hidden');
        }});
    </script>
</body>
</html>"""


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "AI Product"
    value_prop = sys.argv[2] if len(sys.argv) > 2 else "The future of building."
    features = sys.argv[3].split(",") if len(sys.argv) > 3 else ["Feature 1", "Feature 2", "Feature 3"]

    html = generate_lp(name, value_prop, features)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "output", "lp")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "index.html")

    with open(output_path, "w") as f:
        f.write(html)

    print(f"LP generated: {output_path}")
