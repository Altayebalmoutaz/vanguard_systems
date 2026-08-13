"""
PHI eligibility persistence (checks, estimates, audit, request queue).

The direct-Postgres (psycopg) path is the single production code path: when
``DATABASE_URL`` (legacy alias ``NEON_DATABASE_URL``) is configured — for the
Supabase-only pilot it points at the Supabase Postgres — all reads/writes go
through :mod:`app.db.connection`. The Supabase PostgREST branches below survive
only for local dev / unit tests without a DSN; the production startup guard
(:mod:`app.startup_guards`) requires the DSN, so they can never run in prod.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.config import get_settings as get_app_settings
from app.db.connection import get_neon_dsn, neon_connection
from app.db.json_safe import json_safe
from app.db.phi_store import require_practice_id_for_neon
from app.eligibility.config import EligibilitySettings
from app.eligibility.sanitize import scrub_detail_for_storage
from supabase import Client

logger = logging.getLogger(__name__)

_JSONB_KEYS = frozenset(
    {
        "raw_response",
        "detail",
        "input_json",
        "output_json",
        "vob_details",
    }
)


def _resolve_settings(settings: Settings | EligibilitySettings | None) -> Settings:
    if settings is None:
        return get_app_settings()
    if isinstance(settings, Settings):
        return settings
    neon_value = getattr(settings, "neon_database_url", None)
    neon = neon_value.strip() if isinstance(neon_value, str) else ""
    if neon:
        return Settings(
            neon_database_url=neon,
            supabase_url=settings.supabase_url or None,
            supabase_service_role_key=settings.supabase_key or None,
        )
    supabase_url = getattr(settings, "supabase_url", None)
    supabase_key = getattr(settings, "supabase_key", None)
    return Settings(
        neon_database_url="",
        supabase_url=supabase_url if isinstance(supabase_url, str) else None,
        supabase_service_role_key=supabase_key if isinstance(supabase_key, str) else None,
    )


def _use_neon(settings: Settings | EligibilitySettings | None) -> bool:
    return bool(get_neon_dsn(_resolve_settings(settings)))


def _practice_id_from(*, practice_id: str | None, row: dict[str, Any] | None = None) -> str:
    pid = (practice_id or "").strip()
    if pid:
        return pid
    if row is not None:
        pid = str(row.get("practice_id") or "").strip()
    return pid


def _require_neon_practice_id(
    settings: Settings | EligibilitySettings | None,
    *,
    practice_id: str | None = None,
    row: dict[str, Any] | None = None,
) -> str:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id, row=row)
    if _use_neon(s):
        return require_practice_id_for_neon(pid, row=row, settings=s)
    return pid


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _json_safe(value: Any) -> Any:
    """Recursively coerce values for psycopg Jsonb (no datetime/date/UUID objects)."""
    return json_safe(value)


def _prepare_insert_payload(row: dict[str, Any], *, practice_id: str) -> dict[str, Any]:
    payload = dict(row)
    payload["practice_id"] = practice_id
    prepared: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _JSONB_KEYS:
            prepared[key] = Jsonb(_json_safe(value if value is not None else {}))
        elif key in ("patient_id", "eligibility_check_id", "request_id") and value is not None:
            prepared[key] = UUID(str(value))
        else:
            prepared[key] = value
    return prepared


def _neon_insert(
    settings: Settings,
    *,
    practice_id: str,
    table: str,
    row: dict[str, Any],
    schema: str = "rcm",
    returning: str = "id",
) -> dict[str, Any] | None:
    payload = _prepare_insert_payload(row, practice_id=practice_id)
    cols = list(payload.keys())
    placeholders = ", ".join(f"%({c})s" for c in cols)
    col_list = ", ".join(cols)
    sql = f"insert into {schema}.{table} ({col_list}) values ({placeholders}) returning {returning}"
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, payload)
            result = cur.fetchone()
        conn.commit()
    return dict(result) if result else None


def _neon_update(
    settings: Settings,
    *,
    practice_id: str,
    table: str,
    updates: dict[str, Any],
    where_sql: str,
    where_params: tuple[Any, ...],
    schema: str = "rcm",
) -> None:
    set_parts = [f"{k} = %s" for k in updates]
    params: list[Any] = list(updates.values()) + list(where_params)
    sql = f"update {schema}.{table} set {', '.join(set_parts)} where {where_sql}"
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def _neon_fetchone(
    settings: Settings,
    *,
    practice_id: str | None,
    sql: str,
    params: tuple[Any, ...] | list[Any],
    bypass_rls: bool = False,
) -> dict[str, Any] | None:
    with (
        neon_connection(
            settings,
            practice_id=practice_id,
            bypass_rls=bypass_rls,
        ) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(sql, params)
        row = cur.fetchone()
    return _serialize_row(dict(row)) if row else None


def _neon_fetchall(
    settings: Settings,
    *,
    practice_id: str | None,
    sql: str,
    params: tuple[Any, ...] | list[Any],
    bypass_rls: bool = False,
) -> list[dict[str, Any]]:
    with (
        neon_connection(
            settings,
            practice_id=practice_id,
            bypass_rls=bypass_rls,
        ) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_serialize_row(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# eligibility_checks
# ---------------------------------------------------------------------------
def _get_latest_eligibility_check_neon(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID,
    payer_id: str,
) -> dict[str, Any] | None:
    return _neon_fetchone(
        settings,
        practice_id=practice_id,
        sql="""
            select *
            from rcm.eligibility_checks
            where practice_id = %s
              and patient_id = %s
              and payer_id = %s
            order by checked_at desc
            limit 1
        """,
        params=(practice_id, patient_id, payer_id),
    )


def _get_latest_eligibility_check_supabase(
    supabase: Client, patient_id: UUID, payer_id: str
) -> dict[str, Any] | None:
    res = (
        supabase.table("eligibility_checks")
        .select("*")
        .eq("patient_id", str(patient_id))
        .eq("payer_id", payer_id)
        .order("checked_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_latest_eligibility_check(
    supabase: Client,
    patient_id: UUID,
    payer_id: str,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        return _get_latest_eligibility_check_neon(
            s, practice_id=pid, patient_id=patient_id, payer_id=payer_id
        )
    return _get_latest_eligibility_check_supabase(supabase, patient_id, payer_id)


def _get_eligibility_check_by_id_neon(
    settings: Settings,
    *,
    practice_id: str,
    check_id: UUID,
) -> dict[str, Any] | None:
    return _neon_fetchone(
        settings,
        practice_id=practice_id,
        sql="select * from rcm.eligibility_checks where practice_id = %s and id = %s limit 1",
        params=(practice_id, check_id),
    )


def _get_eligibility_check_by_id_supabase(
    supabase: Client, check_id: UUID
) -> dict[str, Any] | None:
    res = (
        supabase.table("eligibility_checks").select("*").eq("id", str(check_id)).limit(1).execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_eligibility_check_by_id(
    supabase: Client,
    check_id: UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        return _get_eligibility_check_by_id_neon(s, practice_id=pid, check_id=check_id)
    return _get_eligibility_check_by_id_supabase(supabase, check_id)


def _insert_eligibility_check_neon(
    settings: Settings,
    *,
    practice_id: str,
    row: dict[str, Any],
) -> UUID:
    result = _neon_insert(settings, practice_id=practice_id, table="eligibility_checks", row=row)
    if not result or not result.get("id"):
        raise RuntimeError("eligibility_checks insert returned no data")
    return UUID(str(result["id"]))


def _insert_eligibility_check_supabase(supabase: Client, row: dict[str, Any]) -> UUID:
    res = supabase.table("eligibility_checks").insert(row).execute()
    data = res.data
    if not data:
        raise RuntimeError("eligibility_checks insert returned no data")
    rid = data[0].get("id")
    return UUID(str(rid))


def insert_eligibility_check(
    supabase: Client,
    row: dict[str, Any],
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> UUID:
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id, row=row)
    if _use_neon(s):
        return _insert_eligibility_check_neon(s, practice_id=pid, row=row)
    return _insert_eligibility_check_supabase(supabase, row)


# ---------------------------------------------------------------------------
# procedure_estimates
# ---------------------------------------------------------------------------
def _insert_procedure_estimates_neon(
    settings: Settings,
    *,
    practice_id: str,
    eligibility_check_id: UUID,
    rows: list[dict[str, Any]],
) -> None:
    for r in rows:
        item = {"eligibility_check_id": str(eligibility_check_id), **r}
        _neon_insert(settings, practice_id=practice_id, table="procedure_estimates", row=item)


def _insert_procedure_estimates_supabase(
    supabase: Client, eligibility_check_id: UUID, rows: list[dict[str, Any]]
) -> None:
    payload = []
    for r in rows:
        item = {"eligibility_check_id": str(eligibility_check_id), **r}
        payload.append(item)
    supabase.table("procedure_estimates").insert(payload).execute()


def insert_procedure_estimates(
    supabase: Client,
    eligibility_check_id: UUID,
    rows: list[dict[str, Any]],
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    if not rows:
        return
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id, row=rows[0] if rows else None)
    if _use_neon(s):
        _insert_procedure_estimates_neon(
            s, practice_id=pid, eligibility_check_id=eligibility_check_id, rows=rows
        )
        return
    _insert_procedure_estimates_supabase(supabase, eligibility_check_id, rows)


def _list_procedure_estimates_neon(
    settings: Settings,
    *,
    practice_id: str,
    eligibility_check_id: UUID,
) -> list[dict[str, Any]]:
    return _neon_fetchall(
        settings,
        practice_id=practice_id,
        sql="""
            select *
            from rcm.procedure_estimates
            where practice_id = %s
              and eligibility_check_id = %s
        """,
        params=(practice_id, eligibility_check_id),
    )


def _list_procedure_estimates_supabase(
    supabase: Client, eligibility_check_id: UUID
) -> list[dict[str, Any]]:
    res = (
        supabase.table("procedure_estimates")
        .select("*")
        .eq("eligibility_check_id", str(eligibility_check_id))
        .execute()
    )
    return list(res.data or [])


def list_procedure_estimates(
    supabase: Client,
    eligibility_check_id: UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> list[dict[str, Any]]:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        return _list_procedure_estimates_neon(
            s, practice_id=pid, eligibility_check_id=eligibility_check_id
        )
    return _list_procedure_estimates_supabase(supabase, eligibility_check_id)


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------
def _insert_audit_log_neon(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID | None,
    event_type: str,
    detail: dict[str, Any],
) -> None:
    _neon_insert(
        settings,
        practice_id=practice_id,
        schema="logs",
        table="eligibility_audit_log",
        row={
            "patient_id": str(patient_id) if patient_id else None,
            "event_type": event_type,
            "detail": detail,
        },
    )


def _insert_audit_log_supabase(
    supabase: Client,
    *,
    patient_id: UUID | None,
    event_type: str,
    detail: dict[str, Any],
) -> None:
    supabase.table("eligibility_audit_log").insert(
        {
            "patient_id": str(patient_id) if patient_id else None,
            "event_type": event_type,
            "detail": detail,
        }
    ).execute()


def insert_audit_log(
    supabase: Client,
    *,
    patient_id: UUID | None,
    event_type: str,
    detail: dict[str, Any],
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    safe_detail = scrub_detail_for_storage(detail)
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id)
    if _use_neon(s):
        _insert_audit_log_neon(
            s,
            practice_id=pid,
            patient_id=patient_id,
            event_type=event_type,
            detail=safe_detail,
        )
        return
    _insert_audit_log_supabase(
        supabase,
        patient_id=patient_id,
        event_type=event_type,
        detail=safe_detail,
    )


def _get_latest_eligibility_for_patient_neon(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID,
) -> dict[str, Any] | None:
    return _neon_fetchone(
        settings,
        practice_id=practice_id,
        sql="""
            select *
            from rcm.eligibility_checks
            where practice_id = %s
              and patient_id = %s
            order by checked_at desc
            limit 1
        """,
        params=(practice_id, patient_id),
    )


def _get_latest_eligibility_for_patient_supabase(
    supabase: Client, patient_id: UUID
) -> dict[str, Any] | None:
    res = (
        supabase.table("eligibility_checks")
        .select("*")
        .eq("patient_id", str(patient_id))
        .order("checked_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_latest_eligibility_for_patient(
    supabase: Client,
    patient_id: UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        return _get_latest_eligibility_for_patient_neon(s, practice_id=pid, patient_id=patient_id)
    return _get_latest_eligibility_for_patient_supabase(supabase, patient_id)


def _list_audit_for_patient_neon(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID,
) -> list[dict[str, Any]]:
    return _neon_fetchall(
        settings,
        practice_id=practice_id,
        sql="""
            select *
            from logs.eligibility_audit_log
            where practice_id = %s
              and patient_id = %s
            order by created_at desc
            limit 500
        """,
        params=(practice_id, patient_id),
    )


def _list_audit_for_patient_supabase(supabase: Client, patient_id: UUID) -> list[dict[str, Any]]:
    res = (
        supabase.table("eligibility_audit_log")
        .select("*")
        .eq("patient_id", str(patient_id))
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return list(res.data or [])


def list_audit_for_patient(
    supabase: Client,
    patient_id: UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> list[dict[str, Any]]:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        return _list_audit_for_patient_neon(s, practice_id=pid, patient_id=patient_id)
    return _list_audit_for_patient_supabase(supabase, patient_id)


# ---------------------------------------------------------------------------
# Retry-worker helpers
# ---------------------------------------------------------------------------
def _get_eligibility_agent_settings_neon(
    settings: Settings,
    *,
    practice_id: str | None,
) -> dict[str, Any] | None:
    if practice_id:
        return _neon_fetchone(
            settings,
            practice_id=practice_id,
            sql="select * from rcm.eligibility_agent_settings where practice_id = %s limit 1",
            params=(practice_id,),
        )
    rows = _neon_fetchall(
        settings,
        practice_id=None,
        bypass_rls=True,
        sql="select * from rcm.eligibility_agent_settings limit 1",
        params=(),
    )
    return rows[0] if rows else None


def _get_eligibility_agent_settings_supabase(supabase: Client) -> dict[str, Any] | None:
    res = supabase.table("eligibility_agent_settings").select("*").limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def get_eligibility_agent_settings(
    supabase: Client,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    if _use_neon(s):
        return _get_eligibility_agent_settings_neon(s, practice_id=practice_id)
    return _get_eligibility_agent_settings_supabase(supabase)


def _fetch_retryable_requests_neon(
    settings: Settings,
    *,
    now_iso: str,
    limit: int,
) -> list[dict[str, Any]]:
    return _neon_fetchall(
        settings,
        practice_id=None,
        bypass_rls=True,
        sql="""
            select id, practice_id, patient_id, first_name, last_name, dob, subscriber_id,
                   primary_payer_id, secondary_payer_id, plan_id, cdt_codes, trigger_event,
                   attempt_count, max_attempts, next_retry_at, status
            from rcm.eligibility_requests
            where status = 'retrying'
              and next_retry_at <= %s
            order by next_retry_at asc
            limit %s
        """,
        params=(now_iso, limit),
    )


def _fetch_retryable_requests_supabase(
    supabase: Client, *, now_iso: str, limit: int
) -> list[dict[str, Any]]:
    res = (
        supabase.table("eligibility_requests")
        .select("id, attempt_count, max_attempts, next_retry_at, status")
        .eq("status", "retrying")
        .lte("next_retry_at", now_iso)
        .order("next_retry_at", desc=False)
        .limit(limit)
        .execute()
    )
    return list(res.data or [])


def fetch_retryable_requests(
    supabase: Client,
    *,
    now_iso: str,
    limit: int = 20,
    settings: Settings | EligibilitySettings | None = None,
) -> list[dict[str, Any]]:
    s = _resolve_settings(settings)
    if _use_neon(s):
        return _fetch_retryable_requests_neon(s, now_iso=now_iso, limit=limit)
    return _fetch_retryable_requests_supabase(supabase, now_iso=now_iso, limit=limit)


def _requeue_eligibility_request_neon(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_requests",
        updates={
            "status": "queued",
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": None,
            "status_reason": "Re-queued by eligibility retry worker",
        },
        where_sql="practice_id = %s and id = %s",
        where_params=(practice_id, UUID(str(request_id))),
    )


def _requeue_eligibility_request_supabase(supabase: Client, request_id: str | UUID) -> None:
    (
        supabase.table("eligibility_requests")
        .update(
            {
                "status": "queued",
                "next_retry_at": None,
                "locked_at": None,
                "locked_by": None,
                "status_reason": "Re-queued by eligibility retry worker",
            }
        )
        .eq("id", str(request_id))
        .execute()
    )


def requeue_eligibility_request(
    supabase: Client,
    request_id: str | UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id)
    if _use_neon(s):
        _requeue_eligibility_request_neon(s, practice_id=pid, request_id=request_id)
        return
    _requeue_eligibility_request_supabase(supabase, request_id)


def _fail_eligibility_request_exhausted_neon(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_requests",
        updates={
            "status": "failed",
            "failure_category": "unknown",
            "status_reason": "Automatic retry attempts exhausted",
            "suggested_action": "Review the request and retry manually if appropriate.",
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": None,
        },
        where_sql="practice_id = %s and id = %s",
        where_params=(practice_id, UUID(str(request_id))),
    )


def _fail_eligibility_request_exhausted_supabase(supabase: Client, request_id: str | UUID) -> None:
    (
        supabase.table("eligibility_requests")
        .update(
            {
                "status": "failed",
                "failure_category": "unknown",
                "status_reason": "Automatic retry attempts exhausted",
                "suggested_action": "Review the request and retry manually if appropriate.",
                "next_retry_at": None,
                "locked_at": None,
                "locked_by": None,
            }
        )
        .eq("id", str(request_id))
        .execute()
    )


def fail_eligibility_request_exhausted(
    supabase: Client,
    request_id: str | UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id)
    if _use_neon(s):
        _fail_eligibility_request_exhausted_neon(s, practice_id=pid, request_id=request_id)
        return
    _fail_eligibility_request_exhausted_supabase(supabase, request_id)


def _insert_eligibility_request_event_neon(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
    event_type: str,
    detail: dict[str, Any] | None,
) -> None:
    _neon_insert(
        settings,
        practice_id=practice_id,
        table="eligibility_request_events",
        row={
            "request_id": str(request_id),
            "event_type": event_type,
            "detail": detail or {},
        },
    )


def _insert_eligibility_request_event_supabase(
    supabase: Client,
    request_id: str | UUID,
    event_type: str,
    detail: dict[str, Any] | None = None,
    *,
    practice_id: str | None = None,
) -> None:
    row = {
        "request_id": str(request_id),
        "event_type": event_type,
        "detail": detail or {},
    }
    if practice_id:
        row["practice_id"] = practice_id
    (supabase.table("eligibility_request_events").insert(row).execute())


def insert_eligibility_request_event(
    supabase: Client,
    request_id: str | UUID,
    event_type: str,
    detail: dict[str, Any] | None = None,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    pid = _require_neon_practice_id(s, practice_id=practice_id)
    if _use_neon(s):
        _insert_eligibility_request_event_neon(
            s,
            practice_id=pid,
            request_id=request_id,
            event_type=event_type,
            detail=detail,
        )
        return
    _insert_eligibility_request_event_supabase(
        supabase,
        request_id,
        event_type,
        detail,
        practice_id=pid or None,
    )


def fetch_eligibility_request_row(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    if not _use_neon(s):
        return None
    return _neon_fetchone(
        s,
        practice_id=practice_id,
        sql="select * from rcm.eligibility_requests where practice_id = %s and id = %s limit 1",
        params=(practice_id, UUID(str(request_id))),
    )


def lock_eligibility_request_for_processing(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
    attempt_count: int,
    locked_by: str,
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_requests",
        updates={
            "status": "processing",
            "status_reason": "Calling eligibility agent",
            "error_message": None,
            "error_code": None,
            "suggested_action": None,
            "failure_category": None,
            "attempt_count": attempt_count,
            "started_at": datetime.now(UTC),
            "last_attempt_at": datetime.now(UTC),
            "locked_at": datetime.now(UTC),
            "locked_by": locked_by,
            "next_retry_at": None,
        },
        where_sql="practice_id = %s and id = %s and status = 'queued'",
        where_params=(practice_id, UUID(str(request_id))),
    )


def complete_eligibility_request_processing(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
    primary_check_id: str | None,
    secondary_check_id: str | None,
    output_json: dict[str, Any],
    agent_http_status: int | None,
    agent_duration_ms: int | None,
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_requests",
        updates={
            "status": "completed",
            "status_reason": "Eligibility agent completed",
            "primary_check_id": UUID(str(primary_check_id)) if primary_check_id else None,
            "secondary_check_id": UUID(str(secondary_check_id)) if secondary_check_id else None,
            "output_json": Jsonb(_json_safe(output_json)),
            "error_message": None,
            "error_code": None,
            "suggested_action": None,
            "failure_category": None,
            "agent_http_status": agent_http_status,
            "agent_duration_ms": agent_duration_ms,
            "locked_at": None,
            "locked_by": None,
            "completed_at": datetime.now(UTC),
        },
        where_sql="practice_id = %s and id = %s",
        where_params=(practice_id, UUID(str(request_id))),
    )


def merge_eligibility_request_output_json(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
    patch: dict[str, Any],
) -> None:
    """Shallow-merge ``patch`` into ``rcm.eligibility_requests.output_json``."""
    s = _resolve_settings(settings)
    with neon_connection(s, practice_id=practice_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update rcm.eligibility_requests
                set output_json = coalesce(output_json, '{}'::jsonb) || %s,
                    updated_at = now()
                where practice_id = %s and id = %s
                """,
                (Jsonb(_json_safe(patch)), practice_id, UUID(str(request_id))),
            )
        conn.commit()


def fail_eligibility_request_processing(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
    terminal_status: str,
    failure_category: str,
    error_message: str,
    error_code: str,
    suggested_action: str,
    agent_http_status: int | None,
    agent_duration_ms: int | None,
    next_retry_at: datetime | None,
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_requests",
        updates={
            "status": terminal_status,
            "status_reason": error_message,
            "error_message": error_message,
            "error_code": error_code,
            "suggested_action": suggested_action,
            "failure_category": failure_category,
            "agent_http_status": agent_http_status,
            "agent_duration_ms": agent_duration_ms,
            "locked_at": None,
            "locked_by": None,
            "next_retry_at": next_retry_at,
        },
        where_sql="practice_id = %s and id = %s",
        where_params=(practice_id, UUID(str(request_id))),
    )


def _update_eligibility_check_fields_supabase(
    supabase: Client,
    check_id: str | UUID,
    *,
    routing_status: str,
    response_complete: bool,
    missing_fields: list[str],
) -> None:
    (
        supabase.table("eligibility_checks")
        .update(
            {
                "routing_status": routing_status,
                "response_complete": response_complete,
                "missing_fields": missing_fields,
            }
        )
        .eq("id", str(check_id))
        .execute()
    )


def update_eligibility_check_fields(
    supabase: Client,
    check_id: str | UUID,
    *,
    routing_status: str,
    response_complete: bool,
    missing_fields: list[str],
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        _neon_update(
            s,
            practice_id=pid,
            table="eligibility_checks",
            updates={
                "routing_status": routing_status,
                "response_complete": response_complete,
                "missing_fields": missing_fields,
            },
            where_sql="practice_id = %s and id = %s",
            where_params=(pid, UUID(str(check_id))),
        )
        return
    if _use_neon(s) and not pid:
        raise RuntimeError("practice_id required for Neon PHI store")
    _update_eligibility_check_fields_supabase(
        supabase,
        check_id,
        routing_status=routing_status,
        response_complete=response_complete,
        missing_fields=missing_fields,
    )


def _complete_eligibility_request_after_voice_supabase(
    supabase: Client,
    request_id: str | UUID,
    *,
    primary_check_id: str | UUID,
    completed_at: str | datetime,
) -> None:
    (
        supabase.table("eligibility_requests")
        .update(
            {
                "primary_check_id": str(primary_check_id),
                "status": "completed",
                "status_reason": "Eligibility complete (Stedi + voice verification)",
                "completed_at": completed_at,
            }
        )
        .eq("id", str(request_id))
        .execute()
    )


def complete_eligibility_request_after_voice(
    supabase: Client,
    request_id: str | UUID,
    *,
    primary_check_id: str | UUID,
    completed_at: str | datetime,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    pid = _practice_id_from(practice_id=practice_id)
    if _use_neon(s) and pid:
        _neon_update(
            s,
            practice_id=pid,
            table="eligibility_requests",
            updates={
                "primary_check_id": UUID(str(primary_check_id)),
                "status": "completed",
                "status_reason": "Eligibility complete (Stedi + voice verification)",
                "completed_at": completed_at,
            },
            where_sql="practice_id = %s and id = %s",
            where_params=(pid, UUID(str(request_id))),
        )
        return
    if _use_neon(s) and not pid:
        raise RuntimeError("practice_id required for Neon PHI store")
    _complete_eligibility_request_after_voice_supabase(
        supabase,
        request_id,
        primary_check_id=primary_check_id,
        completed_at=completed_at,
    )


def touch_eligibility_agent_settings_sync(
    settings: Settings,
    *,
    practice_id: str,
    next_retry_at: datetime | None = None,
) -> None:
    updates: dict[str, Any] = {"last_sync_at": datetime.now(UTC)}
    if next_retry_at is not None:
        updates["next_retry_at"] = next_retry_at
    _neon_update(
        settings,
        practice_id=practice_id,
        table="eligibility_agent_settings",
        updates=updates,
        where_sql="practice_id = %s",
        where_params=(practice_id,),
    )
