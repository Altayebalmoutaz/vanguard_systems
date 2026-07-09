"""Application Postgres connections (Supabase Postgres for the Supabase-only pilot).

Historically this module targeted a dedicated Neon PHI plane. The pilot now runs on a
single Supabase Postgres; ``DATABASE_URL`` is the canonical DSN and
``NEON_DATABASE_URL`` remains a back-compat alias (see ``app.config.Settings``).
The ``neon_*`` symbol names are kept to avoid churn across the codebase.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import json
import time
from urllib.parse import urlparse

import psycopg
from psycopg import Connection

from app.config import Settings
from app.db.tenancy import set_tenant_gucs


class NeonNotConfiguredError(RuntimeError):
    """Raised when Postgres is required but ``DATABASE_URL`` is unset."""


# Forward-looking alias; new code should prefer this name.
DatabaseNotConfiguredError = NeonNotConfiguredError


# region agent log
def _agent_debug_log(hypothesis_id: str, message: str, data: dict) -> None:
    try:
        with open("debug-c16f79.log", "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": "c16f79",
                        "runId": "initial",
                        "hypothesisId": hypothesis_id,
                        "location": "app/db/connection.py",
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    default=str,
                )
                + "\n"
            )
    except Exception:
        pass


def _safe_dsn_info(dsn: str | None) -> dict:
    if not dsn:
        return {"configured": False}
    try:
        parsed = urlparse(dsn)
        return {
            "configured": True,
            "scheme": parsed.scheme,
            "host": parsed.hostname,
            "port": parsed.port,
            "username": parsed.username,
            "hasPassword": bool(parsed.password),
            "queryKeys": sorted(k for k in parsed.query.split("&") if k),
        }
    except Exception as exc:
        return {"configured": True, "parseError": type(exc).__name__}
# endregion


def get_neon_dsn(settings: Settings) -> str | None:
    url = (settings.neon_database_url or "").strip()
    return url or None


def require_neon_dsn(settings: Settings) -> str:
    dsn = get_neon_dsn(settings)
    # region agent log
    _agent_debug_log("H4", "database dsn resolved", _safe_dsn_info(dsn))
    # endregion
    if not dsn:
        raise NeonNotConfiguredError(
            "DATABASE_URL (or legacy NEON_DATABASE_URL) is not configured"
        )
    return dsn


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
def neon_connection(
    settings: Settings,
    *,
    practice_id: str | None = None,
    bypass_rls: bool = False,
) -> Generator[Connection, None, None]:
    """Open a Postgres connection; optionally bind ``app.practice_id`` for RLS."""
    dsn = require_neon_dsn(settings)
    # region agent log
    _agent_debug_log(
        "H4,H5",
        "database connect attempt",
        {**_safe_dsn_info(dsn), "practiceId": practice_id, "bypassRls": bypass_rls},
    )
    # endregion
    try:
        with psycopg.connect(dsn) as conn:
            # region agent log
            _agent_debug_log(
                "H5",
                "database connect success",
                {"practiceId": practice_id, "bypassRls": bypass_rls},
            )
            # endregion
            if practice_id is not None:
                apply_tenant_context(conn, practice_id=practice_id, bypass_rls=bypass_rls)
                # region agent log
                _agent_debug_log("H5", "tenant context applied", {"practiceId": practice_id})
                # endregion
            yield conn
    except Exception as exc:
        # region agent log
        _agent_debug_log(
            "H5",
            "database connect/query failed",
            {
                **_safe_dsn_info(dsn),
                "practiceId": practice_id,
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)[:500],
            },
        )
        # endregion
        raise


# Forward-looking alias; new code should prefer this name.
db_connection = neon_connection
