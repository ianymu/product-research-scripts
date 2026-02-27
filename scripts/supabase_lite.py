"""
Lightweight Supabase client using httpx (no heavy supabase SDK needed).
Drop-in replacement for Oz Cloud Agent environments where pip install supabase fails.

Usage:
    from supabase_lite import SupabaseLite
    sb = SupabaseLite(url, key)
    sb.insert("pain_points", record)  # returns True/False
    sb.select("cycles", "cycle_id", order_by="cycle_id", desc=True, limit=1)
"""
import httpx
import json


class SupabaseLite:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def insert(self, table: str, record: dict) -> bool:
        """Insert a record. Returns True on success, raises on duplicate (23505)."""
        resp = httpx.post(
            f"{self.url}/rest/v1/{table}",
            headers=self.headers,
            json=record,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return True
        if resp.status_code == 409 or "23505" in resp.text:
            raise DuplicateError(resp.text)
        resp.raise_for_status()
        return True

    def select(self, table: str, columns: str = "*", order_by: str = None,
               desc: bool = False, limit: int = None, eq: dict = None) -> list:
        """Simple select query."""
        params = {"select": columns}
        if order_by:
            params["order"] = f"{order_by}.{'desc' if desc else 'asc'}"
        if limit:
            headers = {**self.headers, "Range": f"0-{limit - 1}"}
        else:
            headers = self.headers
        if eq:
            for k, v in eq.items():
                params[k] = f"eq.{v}"
        resp = httpx.get(
            f"{self.url}/rest/v1/{table}",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


class DuplicateError(Exception):
    pass
