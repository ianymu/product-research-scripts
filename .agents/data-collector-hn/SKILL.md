# Data Collector HackerNews — Oz Cloud Agent

## Description
Collects pain point data from HackerNews using the free Algolia API.
One of 4 parallel Oz Cloud Agents dispatched by DataCollector.

## Execution
1. Extract cycle_id from the prompt
2. Run: `python3 scripts/hn_collector.py <cycle_id>`
3. Capture output JSON statistics
4. Return results summary

## Environment Variables Required
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Script failure: capture stderr, report error
- Timeout (30 min): abort and report
- Partial success: report both success and error counts
