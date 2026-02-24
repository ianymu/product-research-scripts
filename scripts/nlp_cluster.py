"""
V7 Pipeline — NLP Clustering
Clusters similar pain points using embeddings + k-means.
"""
import os
import json
import sys
import numpy as np
from sklearn.cluster import KMeans
import openai
from supabase import create_client

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

MIN_CLUSTERS = 10
EMBEDDING_MODEL = "text-embedding-3-small"


def get_embeddings(texts: list) -> list:
    """Get embeddings from OpenAI."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [e.embedding for e in resp.data]


def cluster_cycle(cycle_id: int, min_clusters: int = MIN_CLUSTERS) -> dict:
    """Cluster pain points for a cycle."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    records = sb.table("pain_points").select("id, title, content, category").eq(
        "cycle_id", cycle_id
    ).not_.is_("category", "null").limit(2000).execute().data

    if len(records) < min_clusters:
        return {"error": f"Too few records ({len(records)}) for {min_clusters} clusters"}

    # Generate embeddings
    texts = [(r.get("title", "") + " " + (r.get("content", "") or "")[:500]).strip() for r in records]
    embeddings = get_embeddings(texts)

    # K-means clustering
    n_clusters = min(min_clusters, len(records) // 3)
    X = np.array(embeddings)
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    # Label clusters using representative samples
    clusters = {}
    for i, record in enumerate(records):
        cluster_id = int(labels[i])
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        clusters[cluster_id].append(record)

    # Update Supabase
    results = {"total_clustered": 0, "clusters": []}
    for cluster_id, members in clusters.items():
        label = f"cluster-{cluster_id}"  # LLM labeling done by score_calc
        for member in members:
            sb.table("pain_points").update({
                "cluster_id": cluster_id,
                "cluster_label": label,
            }).eq("id", member["id"]).execute()
            results["total_clustered"] += 1

        results["clusters"].append({
            "cluster_id": cluster_id,
            "label": label,
            "count": len(members),
        })

    return results


if __name__ == "__main__":
    cycle_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Clustering pain points for cycle {cycle_id}...")
    stats = cluster_cycle(cycle_id)
    print(json.dumps(stats, indent=2))
