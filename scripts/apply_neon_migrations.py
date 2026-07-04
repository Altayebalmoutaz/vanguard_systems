"""Apply neon/migrations/*.sql in order against NEON_DATABASE_URL."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "neon" / "migrations"


def migration_files() -> list[Path]:
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name[:3].isdigit())
    if not files:
        raise SystemExit(f"No numbered migrations under {MIGRATIONS_DIR}")
    return files


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    url = os.getenv("NEON_DATABASE_URL")
    if not url:
        raise SystemExit("NEON_DATABASE_URL is not set in .env")

    files = migration_files()
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for path in files:
                cur.execute(path.read_text(encoding="utf-8"))
                print(f"applied {path.name}")


if __name__ == "__main__":
    main()
