"""
V7 Pipeline — Supabase Reader
Generic read utility for any table with filtering.
"""
import os
import json
import sys
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def read_table(table: str, filters: dict = None, order_by: str = None,
               ascending: bool = False, limit: int = 100, select: str = "*") -> list:
    """Read from Supabase table with optional filters."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    query = sb.table(table).select(select)

    if filters:
        for key, value in filters.items():
            query = query.eq(key, value)

    if order_by:
        query = query.order(order_by, desc=not ascending)

    query = query.limit(limit)
    result = query.execute()
    return result.data


if __name__ == "__main__":
    table = sys.argv[1] if len(sys.argv) > 1 else "pain_points"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    data = read_table(table, limit=limit)
    print(json.dumps(data, indent=2, default=str))
