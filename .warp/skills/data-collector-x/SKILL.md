# Data Collector X/Twitter — Oz Cloud Agent

## Description
Collects pain point data from X/Twitter using Apify (apidojo/tweet-scraper).
One of 4 parallel Oz Cloud Agents dispatched by DataCollector.

## Execution
1. Extract cycle_id from the prompt
2. Run: `python3 scripts/apify_x.py <cycle_id>`
3. Capture output JSON statistics
4. Return results summary

## Account Configuration
### Tier 0 (21 accounts, no engagement filter)
levelsio, dannypostma, marclouvion, mckaywrigley, tibo_maker,
OpenAI, AnthropicAI, GoogleDeepMind, xai, MistralAI,
perplexity_ai, karpathy, sama, ylecun, drjimfan, AndrewYNg,
ProductHunt, ycombinator, paulg, naval, garrytan

### Filtered (8 accounts, engagement threshold)
bcherny, _catwu, cursor_ai, _akhaliq, rowancheung,
lennysan, gregisenberg, Jason

## Keyword Searches (5 queries)
- "I wish there was" OR "someone should build"
- "paying for" AND "frustrating"
- "switched from" AND "alternative"
- "went viral" OR "million users"
- "shut down" OR "raised funding"

## Environment Variables Required
- APIFY_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Per-batch retry on failure
- Skip failed batches, continue others
- Age filter: 15 days max (Twitter date format)
