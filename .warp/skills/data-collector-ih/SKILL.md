# Data Collector IndieHackers — Oz Cloud Agent

## Description
Collects pain point data from IndieHackers using Apify Web Scraper.
One of 4 parallel Oz Cloud Agents dispatched by DataCollector.

## Execution
1. Extract cycle_id from the prompt
2. Run: `python3 scripts/apify_web.py <cycle_id>`
3. Capture output JSON statistics
4. Return results summary

## Environment Variables Required
- APIFY_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Script failure: capture stderr, report error
- Timeout (60 min): abort and report
- Partial success: report both success and error counts
