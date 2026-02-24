# Market Validator — Oz Cloud Agent

## Description
Validates market for a specific direction: TAM/SAM/SOM calculation, trend analysis.
One agent per direction, run in parallel.

## Capabilities
- Research market size via Perplexity
- Calculate TAM/SAM/SOM
- Analyze market trends
- Write results to Supabase market_validations table

## Execution
1. Parse direction name from task description
2. Run: `python3 scripts/tam_calc.py [cycle_id] [direction_id] "[direction_name]"`
3. Return structured TAM/SAM/SOM with sources

## Environment Variables Required
- PERPLEXITY_API_KEY
- ANTHROPIC_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Error Handling
- Perplexity timeout: retry once
- Low confidence data: flag in output
