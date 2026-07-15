"""Database helpers for payer voice verification sessions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import Settings
from app.eligibility.config import EligibilitySettings
from app.eligibility.db import get_supabase
from app.eligibility.db_phi import (
    _neon_fetchall,
    _neon_fetchone,
    _neon_insert,
    _neon_update,
    _practice_id_from,
    _resolve_settings,
    _use_neon,
)
from supabase import Client

OPEN_SESSION_STATUSES = (
    "queued",
    "calling",
    "completed",
    "pending_review",
)

_SESSION_JSONB_KEYS = frozenset({"extracted_fields"})


def _require_neon_practice_id(
    settings: Settings | EligibilitySettings | None,
    *,
    practice_id: str | None = None,
    row: dict[str, Any] | None = None,
) -> str:
    s = _resolve_settings(settings)
    if not _use_neon(s):
        return _practice_id_from(practice_id=practice_id, row=row)
    pid = _practice_id_from(practice_id=practice_id, row=row)
    if not pid:
        raise RuntimeError("practice_id required for Neon PHI store")
    return pid


def _prepare_session_updates(values: dict[str, Any]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for key, value in values.items():
        if key in _SESSION_JSONB_KEYS and value is not None:
            prepared[key] = Jsonb(value)
        else:
            prepared[key] = value
    return prepared


def fetch_payer_voice_config(supabase: Client, payer_id: str) -> dict[str, Any] | None:
    pid = (payer_id or "").strip()
    if not pid:
        return None
    res = (
        supabase.table("payer_network")
        .select("payer_id, eligibility_phone, voice_escalation_enabled, display_name")
        .eq("payer_id", pid)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_open_session_for_check_neon(
    settings: Settings,
    *,
    practice_id: str,
    eligibility_check_id: str | UUID,
) -> dict[str, Any] | None:
    placeholders = ", ".join("%s" for _ in OPEN_SESSION_STATUSES)
    return _neon_fetchone(
        settings,
        practice_id=practice_id,
        sql=f"""
            select *
            from rcm.payer_verification_sessions
            where practice_id = %s
              and eligibility_check_id = %s
              and status in ({placeholders})
            order by created_at desc
            limit 1
        """,
        params=(practice_id, UUID(str(eligibility_check_id)), *OPEN_SESSION_STATUSES),
    )


def _fetch_open_session_for_check_supabase(
    supabase: Client, eligibility_check_id: str | UUID
) -> dict[str, Any] | None:
    res = (
        supabase.table("payer_verification_sessions")
        .select("*")
        .eq("eligibility_check_id", str(eligibility_check_id))
        .in_("status", list(OPEN_SESSION_STATUSES))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def fetch_open_session_for_check(
    supabase: Client,
    eligibility_check_id: str | UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    if _use_neon(s):
        pid = _require_neon_practice_id(s, practice_id=practice_id)
        return _fetch_open_session_for_check_neon(
            s,
            practice_id=pid,
            eligibility_check_id=eligibility_check_id,
        )
    return _fetch_open_session_for_check_supabase(supabase, eligibility_check_id)


def _insert_verification_session_neon(
    settings: Settings,
    *,
    practice_id: str,
    row: dict[str, Any],
) -> UUID:
    result = _neon_insert(
        settings,
        practice_id=practice_id,
        table="payer_verification_sessions",
        row=row,
    )
    if not result or not result.get("id"):
        raise RuntimeError("insert payer_verification_sessions returned no row")
    return UUID(str(result["id"]))


def _insert_verification_session_supabase(supabase: Client, row: dict[str, Any]) -> UUID:
    res = supabase.table("payer_verification_sessions").insert(row).execute()
    data = res.data
    if not data:
        raise RuntimeError("insert payer_verification_sessions returned no row")
    return UUID(str(data[0]["id"]))


def insert_verification_session(
    supabase: Client,
    row: dict[str, Any],
    *,
    settings: Settings | EligibilitySettings | None = None,
) -> UUID:
    s = _resolve_settings(settings)
    if _use_neon(s):
        pid = _require_neon_practice_id(s, row=row)
        return _insert_verification_session_neon(s, practice_id=pid, row=row)
    return _insert_verification_session_supabase(supabase, row)


def _fetch_session_by_id_neon(
    settings: Settings,
    *,
    practice_id: str | None,
    session_id: str | UUID,
) -> dict[str, Any] | None:
    if practice_id:
        return _neon_fetchone(
            settings,
            practice_id=practice_id,
            sql="""
                select *
                from rcm.payer_verification_sessions
                where practice_id = %s
                  and id = %s
                limit 1
            """,
            params=(practice_id, UUID(str(session_id))),
        )
    return _neon_fetchone(
        settings,
        practice_id=None,
        bypass_rls=True,
        sql="select * from rcm.payer_verification_sessions where id = %s limit 1",
        params=(UUID(str(session_id)),),
    )


def _fetch_session_by_id_supabase(
    supabase: Client, session_id: str | UUID
) -> dict[str, Any] | None:
    res = (
        supabase.table("payer_verification_sessions")
        .select("*")
        .eq("id", str(session_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def fetch_session_by_id(
    supabase: Client,
    session_id: str | UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    if _use_neon(s):
        return _fetch_session_by_id_neon(
            s,
            practice_id=_practice_id_from(practice_id=practice_id) or None,
            session_id=session_id,
        )
    return _fetch_session_by_id_supabase(supabase, session_id)


def _fetch_queued_sessions_neon(settings: Settings, *, limit: int) -> list[dict[str, Any]]:
    return _neon_fetchall(
        settings,
        practice_id=None,
        bypass_rls=True,
        sql="""
            select *
            from rcm.payer_verification_sessions
            where status = 'queued'
            order by created_at asc
            limit %s
        """,
        params=(limit,),
    )


def _fetch_queued_sessions_supabase(supabase: Client, *, limit: int) -> list[dict[str, Any]]:
    res = (
        supabase.table("payer_verification_sessions")
        .select("*")
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return list(res.data or [])


def fetch_queued_sessions(
    supabase: Client,
    *,
    limit: int = 5,
    settings: Settings | EligibilitySettings | None = None,
) -> list[dict[str, Any]]:
    s = _resolve_settings(settings)
    if _use_neon(s):
        return _fetch_queued_sessions_neon(s, limit=limit)
    return _fetch_queued_sessions_supabase(supabase, limit=limit)


def _update_verification_session_neon(
    settings: Settings,
    *,
    practice_id: str,
    session_id: str | UUID,
    values: dict[str, Any],
) -> None:
    _neon_update(
        settings,
        practice_id=practice_id,
        table="payer_verification_sessions",
        updates=_prepare_session_updates(values),
        where_sql="practice_id = %s and id = %s",
        where_params=(practice_id, UUID(str(session_id))),
    )


def _update_verification_session_supabase(
    supabase: Client, session_id: str | UUID, values: dict[str, Any]
) -> None:
    supabase.table("payer_verification_sessions").update(values).eq("id", str(session_id)).execute()


def update_verification_session(
    supabase: Client,
    session_id: str | UUID,
    values: dict[str, Any],
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> None:
    s = _resolve_settings(settings)
    if _use_neon(s):
        pid = _require_neon_practice_id(s, practice_id=practice_id, row=values)
        _update_verification_session_neon(
            s,
            practice_id=pid,
            session_id=session_id,
            values=values,
        )
        return
    _update_verification_session_supabase(supabase, session_id, values)


def _fetch_eligibility_request_neon(
    settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
) -> dict[str, Any] | None:
    return _neon_fetchone(
        settings,
        practice_id=practice_id,
        sql="select * from rcm.eligibility_requests where practice_id = %s and id = %s limit 1",
        params=(practice_id, UUID(str(request_id))),
    )


def _fetch_eligibility_request_supabase(
    supabase: Client, request_id: str | UUID
) -> dict[str, Any] | None:
    res = (
        supabase.table("eligibility_requests")
        .select("*")
        .eq("id", str(request_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def fetch_eligibility_request(
    supabase: Client,
    request_id: str | UUID,
    *,
    practice_id: str | None = None,
    settings: Settings | EligibilitySettings | None = None,
) -> dict[str, Any] | None:
    s = _resolve_settings(settings)
    if _use_neon(s):
        pid = _require_neon_practice_id(s, practice_id=practice_id)
        return _fetch_eligibility_request_neon(
            s,
            practice_id=pid,
            request_id=request_id,
        )
    return _fetch_eligibility_request_supabase(supabase, request_id)


def get_supabase_client(settings: EligibilitySettings | None = None) -> Client:
    return get_supabase(settings)
