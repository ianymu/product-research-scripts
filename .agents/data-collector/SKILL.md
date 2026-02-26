# Data Collector — Oz Cloud Agent

## Description
Collects pain point data from a single source (Reddit, HN, or IndieHackers).
Called by DataCollector OpenClaw Agent via `oz agent run-cloud`.

## Capabilities
- Reddit: Apify Reddit Scraper (cloud IPs need Apify proxy)
- HackerNews: Algolia API (free, no API key needed)
- IndieHackers: Apify Web Scraper
- Write results to Supabase pain_points table
- Report collection statistics

## Execution
1. Parse the source from the task description
2. Run the corresponding Python script:
   - "Reddit" → `python3 scripts/apify_reddit.py [cycle_id]`
   - "HackerNews" → `python3 scripts/hn_collector.py [cycle_id]`
   - "IndieHackers" → `python3 scripts/apify_web.py [cycle_id]`
3. Capture output statistics
4. Return results

## Environment Variables Required
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- APIFY_API_KEY (Reddit + IndieHackers)

## Error Handling
- Script failure: capture stderr, report error
- Timeout (30 min): abort and report
- Partial success: report both success and error counts
