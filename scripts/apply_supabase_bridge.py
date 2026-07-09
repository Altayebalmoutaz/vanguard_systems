#!/usr/bin/env python3
"""Apply supabase/migrations/053_supabase_dashboard_bridge.sql to Supabase Postgres."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = REPO_ROOT / "supabase" / "migrations" / "053_supabase_dashboard_bridge.sql"


def main() -> int:
    from scripts.apply_neon_migrations import _database_url

    url = _database_url()
    if not url:
        print(
            "Set DATABASE_URL or SUPABASE_DB_PASSWORD (+ SUPABASE_URL) in .env,\n"
            "or paste the SQL in Supabase Dashboard → SQL Editor:\n"
            f"  {SQL_FILE}",
            file=sys.stderr,
        )
        return 1
    if "supabase.co" not in url:
        print(
            "This script targets Supabase Postgres. For Neon, use apply_neon_migrations.py.",
            file=sys.stderr,
        )
        return 1
    if not SQL_FILE.is_file():
        print(f"Missing {SQL_FILE}", file=sys.stderr)
        return 1
    try:
        import psycopg
    except ImportError:
        print("Install psycopg: pip install 'psycopg[binary]'", file=sys.stderr)
        return 1

    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(url, connect_timeout=30, autocommit=True) as conn:
        conn.execute(sql)
    print("Applied 053_supabase_dashboard_bridge.sql successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
