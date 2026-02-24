# Product Research Scripts — V7 Pipeline

Automated scripts for the V7 product research pipeline. Used by Oz Cloud Agents for parallel data collection and analysis.

## Scripts

### Data Collection (Stage 1)
| Script | Source | Description |
|--------|--------|-------------|
| `apify_reddit.py` | Reddit | Scrapes 8 subreddits for SaaS pain points |
| `apify_hn.py` | HackerNews | Scrapes Ask/Show/New HN posts |
| `apify_web.py` | IndieHackers | Scrapes trending and newest posts |
| `apify_x.py` | X/Twitter | Scrapes 16 tier-0 + 5 filtered accounts |

### Analysis (Stage 2)
| Script | Description |
|--------|-------------|
| `llm_classify.py` | Classifies pain points into 8 categories via Claude |
| `nlp_cluster.py` | Clusters similar pain points via embeddings + k-means |
| `score_calc.py` | V7 dual-layer scoring: outer 40 + inner D1-D8 60 = 100 |

### Validation (Stage 3)
| Script | Description |
|--------|-------------|
| `tam_calc.py` | TAM/SAM/SOM calculation via Perplexity + Claude |
| `competitor_report.py` | SWOT, Rogers, funding, Thiel analysis per competitor |
| `landing_page_gen.py` | Generates Tailwind LP with email capture |
| `report_gen.py` | Full A-F validation report |

### Utilities
| Script | Description |
|--------|-------------|
| `perplexity_search.py` | Perplexity deep research API wrapper |
| `supabase_read.py` | Generic Supabase reader with filtering |
| `supabase_write.py` | Generic Supabase writer with upsert |
| `sync_pipeline_status.py` | Pipeline stage status tracker |

## Oz Agent Definitions

```
.agents/
├── data-collector/SKILL.md      # Reddit/HN/IndieHackers collection
├── data-collector-x/SKILL.md    # X/Twitter collection (tier0/filtered)
├── market-validator/SKILL.md    # TAM/SAM/SOM per direction
└── competitor-analyzer/SKILL.md # Per-competitor deep analysis
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in API keys in .env
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `APIFY_API_KEY` | Yes | Apify scraping |
| `SUPABASE_URL` | Yes | Database |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Database auth |
| `ANTHROPIC_API_KEY` | Yes | Claude LLM |
| `OPENAI_API_KEY` | Yes | Embeddings |
| `PERPLEXITY_API_KEY` | Yes | Research |
| `SUPABASE_ANON_KEY` | For LP | Landing page email capture |
| `VERCEL_TOKEN` | For LP | Landing page deployment |

## Usage

Each script is independently runnable:
```bash
python scripts/apify_reddit.py 1        # Scrape Reddit for cycle 1
python scripts/llm_classify.py 1         # Classify cycle 1 pain points
python scripts/score_calc.py 1 0         # Score cluster 0 in cycle 1
python scripts/tam_calc.py 1 dir-001 "AI Code Review"  # Calculate TAM
```

Or import as modules for orchestrator integration.
