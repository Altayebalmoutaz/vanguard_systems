"""Persistence for per-clinic OpenDental connections (``rcm.opendental_connections``).

Backs the dashboard OD control panel and the multi-tenant appointment poller.
Secrets never live in this table: ``customer_key_ref`` names an environment
variable on the backend host that holds the clinic's Customer key.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from app.config import Settings
from app.db.connection import neon_connection, require_neon_dsn

logger = logging.getLogger(__name__)

# Fields the dashboard is allowed to update. Keys are column names.
UPDATABLE_FIELDS = frozenset(
    {
        "display_name",
        "base_url",
        "customer_key_ref",
        "poll_enabled",
        "poll_interval_seconds",
        "poll_window_days",
        "cdt_codes",
        "writeback_enabled",
        "writeback_full",
    }
)


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif key == "id":
            out[key] = str(value)
        else:
            out[key] = value
    out["customer_key_configured"] = bool(
        resolve_customer_key(row.get("customer_key_ref"), fallback=None)
    )
    return out


def resolve_customer_key(ref: str | None, *, fallback: str | None) -> str | None:
    """Resolve a clinic Customer key from the env var named by ``customer_key_ref``."""
    name = (ref or "").strip()
    if name:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return (fallback or "").strip() or None


def list_connections(
    settings: Settings,
    *,
    practice_id: str | None = None,
) -> list[dict[str, Any]]:
    """List connections; scoped to one practice when given, else all (worker use)."""
    require_neon_dsn(settings)
    if practice_id:
        sql = "select * from rcm.opendental_connections where practice_id = %s order by practice_id"
        params: tuple[Any, ...] = (practice_id,)
        kwargs: dict[str, Any] = {"practice_id": practice_id}
    else:
        sql = "select * from rcm.opendental_connections order by practice_id"
        params = ()
        kwargs = {"bypass_rls": True}
    with neon_connection(settings, **kwargs) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_serialize(dict(r)) for r in rows]


def list_enabled_connections(settings: Settings) -> list[dict[str, Any]]:
    require_neon_dsn(settings)
    with (
        neon_connection(settings, bypass_rls=True) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            "select * from rcm.opendental_connections where poll_enabled order by practice_id"
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_connection(
    settings: Settings,
    *,
    practice_id: str,
) -> dict[str, Any] | None:
    require_neon_dsn(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            "select * from rcm.opendental_connections where practice_id = %s limit 1",
            (practice_id,),
        )
        row = cur.fetchone()
    return _serialize(dict(row)) if row else None


def upsert_connection(
    settings: Settings,
    *,
    practice_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Create-or-update a connection row with dashboard-editable fields only."""
    require_neon_dsn(settings)
    fields = {k: v for k, v in updates.items() if k in UPDATABLE_FIELDS}
    cols = ["practice_id", *fields.keys()]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    set_sql = (
        ", ".join(f"{c} = excluded.{c}" for c in fields) or "practice_id = excluded.practice_id"
    )
    sql = f"""
        insert into rcm.opendental_connections ({", ".join(cols)})
        values ({placeholders})
        on conflict (practice_id) do update set {set_sql}
        returning *
    """
    params = {"practice_id": practice_id, **fields}
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("opendental_connections upsert returned no data")
    return _serialize(dict(row))


def record_poll_result(
    settings: Settings,
    *,
    practice_id: str,
    status: str,
    appointments: int | None = None,
    error: str | None = None,
) -> None:
    """Persist the outcome of one poll pass so the dashboard shows live poller state."""
    require_neon_dsn(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update rcm.opendental_connections
                set last_poll_at = %s,
                    last_poll_status = %s,
                    last_poll_appointments = %s,
                    last_error = %s
                where practice_id = %s
                """,
                (datetime.now(UTC), status, appointments, error, practice_id),
            )
        conn.commit()


def record_health(
    settings: Settings,
    *,
    practice_id: str,
    healthy: bool,
    error: str | None = None,
) -> None:
    require_neon_dsn(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update rcm.opendental_connections
                set health_status = %s,
                    health_checked_at = %s,
                    last_error = coalesce(%s, last_error)
                where practice_id = %s
                """,
                ("ok" if healthy else "error", datetime.now(UTC), error, practice_id),
            )
        conn.commit()
