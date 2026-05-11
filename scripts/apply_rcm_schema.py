#!/usr/bin/env python3
"""
Apply supabase/migrations/004_rcm_dashboard.sql to your Supabase Postgres database.

Requires one of:
  - DATABASE_URL (full postgresql:// URI), or
  - SUPABASE_URL + SUPABASE_DB_PASSWORD (database password from Supabase
    Dashboard → Project Settings → Database).

Loads environment from the repo root .env (python-dotenv).
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "supabase" / "migrations" / "004_rcm_dashboard.sql"


def _database_url() -> str | None:
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    direct = os.environ.get("DATABASE_URL", "").strip()
    if direct:
        return direct

    pw = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    raw = os.environ.get("SUPABASE_URL", "").strip()
    if not pw or not raw:
        return None
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host.endswith(".supabase.co"):
        return None
    ref = host.removesuffix(".supabase.co")
    return f"postgresql://postgres:{pw}@db.{ref}.supabase.co:5432/postgres"


def main() -> int:
    url = _database_url()
    if not url:
        print(
            "Missing connection: set DATABASE_URL or SUPABASE_DB_PASSWORD (+ SUPABASE_URL) in .env,\n"
            "or run the SQL manually in Supabase → SQL Editor:\n"
            f"  {SQL_FILE}",
            file=sys.stderr,
        )
        return 1
    if not SQL_FILE.is_file():
        print(f"Missing migration file: {SQL_FILE}", file=sys.stderr)
        return 1
    sql = SQL_FILE.read_text(encoding="utf-8")
    try:
        import psycopg
    except ImportError:
        print("Install psycopg: pip install 'psycopg[binary]'", file=sys.stderr)
        return 1

    with psycopg.connect(url, connect_timeout=30, autocommit=True) as conn:
        conn.execute(sql)
    print("Applied 004_rcm_dashboard.sql successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
