# Competitor Analyzer — Oz Cloud Agent

## Description
Deep analysis of a single competitor. One agent per competitor, run in parallel.
Outputs SWOT, Rogers adoption stage, funding history, Thiel comparison.

## Capabilities
- Research competitor via Perplexity
- Generate SWOT matrix
- Assess Rogers adoption stage
- Research funding history
- Compare Thiel monopoly factors
- Write results to Supabase competitor_analyses table

## Execution
1. Parse competitor name from task description
2. Run: `python3 scripts/competitor_report.py [cycle_id] [direction_id] "[competitor_name]"`
3. Return structured analysis

## Environment Variables Required
- PERPLEXITY_API_KEY
- ANTHROPIC_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Insufficient data: mark confidence as low
- Report available data, note gaps
