"""Neon PHI-plane Postgres connections."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import psycopg
from psycopg import Connection

from app.config import Settings
from app.db.tenancy import set_tenant_gucs


class NeonNotConfiguredError(RuntimeError):
    """Raised when PHI-plane Postgres is required but ``NEON_DATABASE_URL`` is unset."""


def get_neon_dsn(settings: Settings) -> str | None:
    url = (settings.neon_database_url or "").strip()
    return url or None


def require_neon_dsn(settings: Settings) -> str:
    dsn = get_neon_dsn(settings)
    if not dsn:
        raise NeonNotConfiguredError("NEON_DATABASE_URL is not configured")
    return dsn


def apply_tenant_context(
    conn: Connection,
    *,
    practice_id: str,
    bypass_rls: bool = False,
) -> None:
    """Set session GUCs so Neon RLS policies can enforce tenant isolation."""
    with conn.cursor() as cur:
        set_tenant_gucs(cur, practice_id=practice_id, bypass_rls=bypass_rls)


@contextmanager
def neon_connection(
    settings: Settings,
    *,
    practice_id: str | None = None,
    bypass_rls: bool = False,
) -> Generator[Connection, None, None]:
    """Open a Neon connection; optionally bind ``app.practice_id`` for RLS."""
    dsn = require_neon_dsn(settings)
    with psycopg.connect(dsn) as conn:
        if practice_id is not None:
            apply_tenant_context(conn, practice_id=practice_id, bypass_rls=bypass_rls)
        yield conn
