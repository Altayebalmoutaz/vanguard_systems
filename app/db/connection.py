"""Application Postgres connection helpers (Supabase for the pilot).

``DATABASE_URL`` is the canonical DSN. ``NEON_DATABASE_URL`` remains a legacy
alias. Symbol names ``neon_*`` / ``NeonNotConfiguredError`` are kept as
back-compat aliases; prefer ``database_*`` / ``DatabaseNotConfiguredError``.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from app.config import Settings
from app.db.tenancy import set_tenant_gucs


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when Postgres is required but ``DATABASE_URL`` is unset."""


# Legacy alias — prefer DatabaseNotConfiguredError in new code.
NeonNotConfiguredError = DatabaseNotConfiguredError


def get_database_dsn(settings: Settings) -> str | None:
    url = (settings.neon_database_url or "").strip()
    return url or None


# Legacy alias — prefer get_database_dsn.
get_neon_dsn = get_database_dsn


def require_database_dsn(settings: Settings) -> str:
    dsn = get_database_dsn(settings)
    if not dsn:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL (or legacy NEON_DATABASE_URL) is not configured"
        )
    return dsn


# Legacy alias — prefer require_database_dsn.
require_neon_dsn = require_database_dsn


def apply_tenant_context(
    conn: Connection,
    *,
    practice_id: str,
    bypass_rls: bool = False,
) -> None:
    """Set session GUCs so Postgres RLS policies can enforce tenant isolation."""
    with conn.cursor() as cur:
        set_tenant_gucs(cur, practice_id=practice_id, bypass_rls=bypass_rls)


@contextmanager
def database_connection(
    settings: Settings,
    *,
    practice_id: str | None = None,
    bypass_rls: bool = False,
) -> Generator[Connection, None, None]:
    """Open a Postgres connection; optionally bind ``app.practice_id`` for RLS."""
    dsn = require_database_dsn(settings)
    with psycopg.connect(dsn) as conn:
        if practice_id is not None or bypass_rls:
            apply_tenant_context(
                conn,
                practice_id=practice_id or "",
                bypass_rls=bypass_rls,
            )
        yield conn


# Legacy alias — prefer database_connection.
neon_connection = database_connection
