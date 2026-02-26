# Data Collector X — Oz Cloud Agent

## Description
Collects pain point data from X/Twitter using Apify.
Adapted from MoltBot X Scraper with tier0/filtered account logic.

## Capabilities
- Scrape X/Twitter via Apify API
- Apply tier0 (no filter) and filtered (engagement threshold) logic
- Filter by max age (15 days)
- Write results to Supabase pain_points table

## Execution
1. Run: `python3 scripts/apify_x.py [cycle_id]`
2. The script handles:
   - 16 tier0 accounts (all tweets, no filter)
   - 5 filtered accounts (likes > 100/500, retweets > 20/50)
   - Age filter (15 days max)
3. Return statistics with tier breakdown

## Account Configuration
### Tier 0 (16 accounts, no filter)
levelsio, dannypostma, marclouvion, mckaywrigley, tibo_maker,
OpenAI, AnthropicAI, GoogleDeepMind, xai, MistralAI,
perplexity_ai, karpathy, sama, ylecun, drjimfan, AndrewYNg

### Filtered (5 accounts, engagement threshold)
bcherny, _catwu, cursor_ai, _akhaliq, rowancheung

## Environment Variables Required
- APIFY_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Per-account retry on failure
- Skip failed accounts, continue others
- Report success/failure per account
