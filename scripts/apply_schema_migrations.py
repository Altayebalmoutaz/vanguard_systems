"""Apply schema/migrations/*.sql in order against DATABASE_URL.

For the Supabase-only pilot, point DATABASE_URL at the Supabase Postgres
connection string (direct or session pooler). NEON_DATABASE_URL remains a
legacy alias for the same DSN.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from app.database_url import resolve_database_url

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "schema" / "migrations"

# Files that only apply to a dedicated Neon project — skip on Supabase Postgres.
SKIP_ON_SUPABASE = frozenset(
    {
        "003_voice_verification.sql",  # payer_network already on Supabase (046)
        "006_pgaudit.sql",  # Neon role settings; use 011_pgaudit_supabase.sql
    }
)


def _database_url() -> str | None:
    load_dotenv(REPO_ROOT / ".env")
    return resolve_database_url(
        database_url=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"),
        supabase_url=os.getenv("SUPABASE_URL"),
        supabase_db_password=os.getenv("SUPABASE_DB_PASSWORD"),
        supabase_pooler_host=os.getenv("SUPABASE_POOLER_HOST"),
    )


def _is_supabase_host(url: str) -> bool:
    return "supabase.co" in url


def migration_files() -> list[Path]:
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name[:3].isdigit())
    if not files:
        raise SystemExit(f"No numbered migrations under {MIGRATIONS_DIR}")
    return files


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    url = _database_url()
    if not url:
        raise SystemExit(
            "DATABASE_URL (or legacy NEON_DATABASE_URL, or SUPABASE_URL + "
            "SUPABASE_DB_PASSWORD) is not set in .env"
        )

    skip_neon_only = _is_supabase_host(url)
    files = migration_files()
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        for path in files:
            if skip_neon_only and path.name in SKIP_ON_SUPABASE:
                print(f"skipped {path.name} (Supabase target)")
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")


if __name__ == "__main__":
    main()
