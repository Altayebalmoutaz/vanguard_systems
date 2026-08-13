"""Neon-backed read/write helpers for dashboard BFF routes."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.audit.access import audit_phi_read
from app.audit.writer import write_audit_log
from app.config import Settings
from app.db.connection import NeonNotConfiguredError, get_neon_dsn, neon_connection
from app.integrations.agent_runs import list_agent_runs_for_patient
from app.pilot.shadow import record_hitl_resolve_shadow
from app.workflow.rcm_tasks import HITL_STATUS_APPROVED, HITL_STATUS_PENDING, HITL_STATUS_REJECTED

logger = logging.getLogger(__name__)

CLEARED_ROUTING_STATUSES = frozenset({"CLEARED", "APPROVED"})


_INFORMATIONAL_INTEGRITY_WARNING_PREFIXES = (
    "layer3_clamp:",
    "important_field_null:",
)


def status_blocking_integrity_warnings(warnings: list[str] | None) -> list[str]:
    if not warnings:
        return []
    return [
        str(w)
        for w in warnings
        if w
        and not any(
            str(w).startswith(prefix) for prefix in _INFORMATIONAL_INTEGRITY_WARNING_PREFIXES
        )
    ]


class DashboardRequestNotFoundError(LookupError):
    """Eligibility request id was not found for the active practice."""


class DashboardPatientNotFoundError(LookupError):
    """Patient id was not found for the active practice."""


class DashboardHitlTaskNotFoundError(LookupError):
    """HITL task id was not found for the active practice."""


class DashboardHitlTaskConflictError(ValueError):
    """HITL task is not in a resolvable state."""


def _require_neon(settings: Settings) -> None:
    if not get_neon_dsn(settings):
        raise NeonNotConfiguredError("DATABASE_URL is not configured")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(val) for key, val in row.items()}


def _priority_rank(priority: str | None) -> int:
    if priority == "high":
        return 1
    if priority == "medium":
        return 2
    return 3


def _coverage_status(
    request_coverage_status: str | None,
    is_active: bool | None,
) -> str:
    if request_coverage_status:
        return request_coverage_status
    if is_active is True:
        return "active"
    if is_active is False:
        return "inactive"
    return "unknown"


def compute_status_label(
    *,
    request_status: str | None,
    is_active: bool | None,
    response_complete: bool | None,
    missing_fields: list[str] | None,
    integrity_warnings: list[str] | None,
    routing_status: str | None,
    has_check: bool,
) -> str:
    status = (request_status or "").strip()
    if status == "failed":
        return "Failed"
    if status == "queued":
        return "Queued"
    if status == "processing":
        return "Processing"
    if status == "retrying":
        return "Retrying"
    if status == "needs_attention":
        return "Needs Attention"
    if is_active is False:
        return "Inactive"
    if not has_check or response_complete is False:
        return "Needs Attention"
    missing_count = len(missing_fields or [])
    warning_count = len(status_blocking_integrity_warnings(integrity_warnings))
    if missing_count > 0 or warning_count > 0:
        return "Needs Attention"
    if routing_status and routing_status not in CLEARED_ROUTING_STATUSES:
        return "Needs Attention"
    return "Verified"


def compute_status_detail(row: dict[str, Any]) -> str | None:
    request_status = str(row.get("request_status") or "")
    if row.get("suggested_action"):
        return str(row["suggested_action"])
    if request_status in {"queued", "processing", "retrying"}:
        reason = row.get("status_reason")
        return str(reason) if reason else request_status
    if request_status == "failed":
        return str(row.get("error_message") or row.get("status_reason") or "Processing failed")
    if row.get("is_active") is False:
        return str(row.get("inactive_reason") or "Coverage inactive")
    if row.get("response_complete") is False:
        return "Payer response is incomplete"
    missing_fields = row.get("missing_fields") or []
    if len(missing_fields) > 0:
        return "Missing normalized eligibility fields"
    integrity_warnings = row.get("integrity_warnings") or []
    if len(status_blocking_integrity_warnings(integrity_warnings)) > 0:
        return "Integrity warnings require review"
    routing_status = row.get("routing_status")
    if routing_status and routing_status not in CLEARED_ROUTING_STATUSES:
        return str(routing_status)
    if request_status == "needs_attention":
        return str(row.get("status_reason") or "Needs attention")
    return "Eligibility verified"


def _shape_dashboard_row(raw: dict[str, Any]) -> dict[str, Any]:
    row = _serialize_row(raw)
    request_status = str(row.get("request_status") or "")
    missing_fields = row.get("missing_fields")
    if missing_fields is None:
        missing_fields_list: list[str] | None = None
    elif isinstance(missing_fields, list):
        missing_fields_list = [str(item) for item in missing_fields]
    else:
        missing_fields_list = None

    integrity_warnings = row.get("integrity_warnings")
    if integrity_warnings is None:
        integrity_warnings_list: list[str] | None = None
    elif isinstance(integrity_warnings, list):
        integrity_warnings_list = [str(item) for item in integrity_warnings]
    else:
        integrity_warnings_list = None

    row["missing_fields"] = missing_fields_list
    row["integrity_warnings"] = integrity_warnings_list
    row["missing_fields_count"] = int(row.get("missing_fields_count") or 0)
    row["integrity_warnings_count"] = int(row.get("integrity_warnings_count") or 0)
    row["priority_rank"] = _priority_rank(str(row.get("priority") or "medium"))
    row["coverage_status"] = _coverage_status(
        row.get("request_coverage_status"),
        row.get("is_active"),
    )
    row["status_label"] = compute_status_label(
        request_status=request_status,
        is_active=row.get("is_active"),
        response_complete=row.get("response_complete"),
        missing_fields=missing_fields_list,
        integrity_warnings=integrity_warnings_list,
        routing_status=row.get("routing_status"),
        has_check=bool(row.get("check_id")),
    )
    row["status_detail"] = compute_status_detail(row)
    row["raw_response"] = None
    row["voice_session_id"] = None
    row["voice_session_status"] = None
    row["voice_merged_check_id"] = None
    row["voice_extracted_fields"] = None
    row["voice_call_reference"] = None
    return row


_QUEUE_SQL = """
with estimate_summary as (
  select eligibility_check_id,
         sum(coalesce(patient_responsibility, 0)) as estimated_patient_responsibility
  from rcm.procedure_estimates
  group by eligibility_check_id
)
select
  er.id as request_id,
  er.patient_id,
  er.first_name,
  er.last_name,
  trim(both from (er.first_name || ' ') || er.last_name) as patient_name,
  er.dob,
  er.subscriber_id,
  er.primary_payer_id,
  -- Prefer OD carrier name so the UI can match payer logos; fall back to Stedi ids.
  coalesce(
    nullif(trim(both from coalesce(er.input_json->>'primary_carrier_name', '')), ''),
    nullif(ec.payer_id, ''),
    er.primary_payer_id
  ) as payer_label,
  er.secondary_payer_id,
  er.plan_id,
  er.cdt_codes,
  er.trigger_event,
  er.status as request_status,
  er.primary_check_id,
  er.secondary_check_id,
  er.error_message,
  er.error_code,
  er.suggested_action,
  er.failure_category,
  er.status_reason,
  er.priority,
  er.appointment_date,
  er.appointment_time,
  er.provider_name,
  er.estimated_claim_value,
  er.coverage_status as request_coverage_status,
  er.attempt_count,
  er.max_attempts,
  er.started_at,
  er.last_attempt_at,
  er.locked_at,
  er.locked_by,
  er.next_retry_at,
  er.parent_request_id,
  er.idempotency_key,
  er.agent_http_status,
  er.agent_duration_ms,
  er.edge_duration_ms,
  er.created_at,
  er.updated_at,
  er.completed_at,
  er.input_json->>'pat_num' as od_pat_num,
  er.input_json->>'source' as request_source,
  er.output_json->'opendental_writeback' as opendental_writeback,
  ec.id as check_id,
  ec.checked_at,
  ec.coverage_order,
  ec.is_active,
  ec.inactive_reason,
  ec.is_covered,
  ec.in_network,
  ec.coverage_percent,
  ec.copay,
  ec.coinsurance,
  ec.deductible_total,
  ec.deductible_met,
  ec.deductible_remaining,
  ec.annual_max_total,
  ec.annual_max_used,
  ec.annual_max_remaining,
  coalesce(es.estimated_patient_responsibility, 0) as estimated_patient_responsibility,
  ec.response_complete,
  coalesce(array_length(ec.missing_fields, 1), 0) as missing_fields_count,
  ec.missing_fields,
  ec.routing_status,
  coalesce(array_length(ec.integrity_warnings, 1), 0) as integrity_warnings_count,
  ec.integrity_warnings,
  coalesce(ec.vob_details, '{}'::jsonb) as vob_details
from rcm.eligibility_requests er
left join rcm.eligibility_checks ec on ec.id = er.primary_check_id
left join estimate_summary es on es.eligibility_check_id = ec.id
where er.practice_id = %s
order by
  case er.priority when 'high' then 1 when 'medium' then 2 else 3 end,
  er.created_at desc
limit %s
"""


def list_eligibility_queue(
    settings: Settings,
    *,
    practice_id: str,
    limit: int = 75,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(_QUEUE_SQL, (practice_id, limit))
        rows = cur.fetchall()
    return [_shape_dashboard_row(dict(row)) for row in rows]


def get_eligibility_agent_settings_row(
    settings: Settings,
    *,
    practice_id: str,
) -> dict[str, Any]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select practice_id, auto_check_enabled, auto_retry_enabled,
                   voice_verification_enabled, voice_verification_auto_queue,
                   last_sync_at, next_retry_at, updated_at
            from rcm.eligibility_agent_settings
            where practice_id = %s
            limit 1
            """,
            (practice_id,),
        )
        row = cur.fetchone()
    if row:
        return _serialize_row(dict(row))
    return {
        "practice_id": practice_id,
        "auto_check_enabled": True,
        "auto_retry_enabled": True,
        "voice_verification_enabled": False,
        "voice_verification_auto_queue": True,
        "last_sync_at": None,
        "next_retry_at": None,
        "updated_at": None,
    }


_ELIGIBILITY_AGENT_SETTINGS_UPDATABLE = frozenset(
    {
        "voice_verification_enabled",
        "voice_verification_auto_queue",
        "auto_check_enabled",
        "auto_retry_enabled",
    }
)


def update_eligibility_agent_settings(
    settings: Settings,
    *,
    practice_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    _require_neon(settings)
    fields = {k: v for k, v in updates.items() if k in _ELIGIBILITY_AGENT_SETTINGS_UPDATABLE}
    if not fields:
        return get_eligibility_agent_settings_row(settings, practice_id=practice_id)
    cols = ["practice_id", *fields.keys()]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    set_sql = ", ".join(f"{c} = excluded.{c}" for c in fields)
    sql = f"""
        insert into rcm.eligibility_agent_settings ({", ".join(cols)})
        values ({placeholders})
        on conflict (practice_id) do update set {set_sql}, updated_at = now()
        returning practice_id, auto_check_enabled, auto_retry_enabled,
                  voice_verification_enabled, voice_verification_auto_queue,
                  last_sync_at, next_retry_at, updated_at
    """
    params = {"practice_id": practice_id, **fields}
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("eligibility_agent_settings upsert returned no data")
    return _serialize_row(dict(row))


def _get_request_primary_check_id(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
) -> UUID | None:
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select primary_check_id
            from rcm.eligibility_requests
            where practice_id = %s and id = %s
            limit 1
            """,
            (practice_id, request_id),
        )
        row = cur.fetchone()
    if not row:
        raise DashboardRequestNotFoundError(f"Eligibility request not found: {request_id}")
    primary_check_id = row.get("primary_check_id")
    return UUID(str(primary_check_id)) if primary_check_id else None


def list_procedure_estimates_for_request(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    check_id = _get_request_primary_check_id(
        settings,
        practice_id=practice_id,
        request_id=request_id,
    )
    if check_id is None:
        return []
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select *
            from rcm.procedure_estimates
            where practice_id = %s
              and eligibility_check_id = %s
            order by created_at asc
            """,
            (practice_id, check_id),
        )
        rows = cur.fetchall()
    return [_serialize_row(dict(row)) for row in rows]


def list_eligibility_request_events(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    _get_request_primary_check_id(settings, practice_id=practice_id, request_id=request_id)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select id, request_id, event_type, detail, created_at
            from rcm.eligibility_request_events
            where practice_id = %s
              and request_id = %s
            order by created_at desc
            limit %s
            """,
            (practice_id, request_id, limit),
        )
        rows = cur.fetchall()
    return [_serialize_row(dict(row)) for row in rows]


def list_eligibility_activity(
    settings: Settings,
    *,
    practice_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select id, request_id, event_type, detail, created_at
            from rcm.eligibility_request_events
            where practice_id = %s
            order by created_at desc
            limit %s
            """,
            (practice_id, limit),
        )
        rows = cur.fetchall()
    return [_serialize_row(dict(row)) for row in rows]


def create_eligibility_request(
    settings: Settings,
    *,
    practice_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _require_neon(settings)
    patient_id = payload.get("patient_id")
    patient_uuid = UUID(str(patient_id)) if patient_id else uuid4()
    insert_row = {
        "practice_id": practice_id,
        "patient_id": patient_uuid,
        "first_name": payload["first_name"],
        "last_name": payload["last_name"],
        "dob": payload["dob"],
        "subscriber_id": payload["subscriber_id"],
        "primary_payer_id": payload["primary_payer_id"],
        "secondary_payer_id": payload.get("secondary_payer_id"),
        "plan_id": payload.get("plan_id"),
        "cdt_codes": payload.get("cdt_codes") or [],
        "trigger_event": payload.get("trigger_event") or "APPOINTMENT_BOOKED",
        "status": "queued",
        "priority": payload.get("priority") or "medium",
        "appointment_date": payload.get("appointment_date"),
        "appointment_time": payload.get("appointment_time"),
        "provider_name": payload.get("provider_name"),
        "estimated_claim_value": payload.get("estimated_claim_value"),
        "idempotency_key": payload.get("idempotency_key"),
        "input_json": payload.get("input_json") or {},
    }
    cols = list(insert_row.keys())
    values_sql = ", ".join(f"%({col})s" for col in cols)
    col_sql = ", ".join(cols)
    sql = f"""
        insert into rcm.eligibility_requests ({col_sql})
        values ({values_sql})
        returning id, patient_id, status, created_at
    """
    params = dict(insert_row)
    params["input_json"] = Jsonb(params["input_json"])
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
    except UniqueViolation as exc:
        raise ValueError("idempotency_conflict") from exc
    if not row:
        raise RuntimeError("eligibility_requests insert returned no data")
    serialized = _serialize_row(dict(row))
    try:
        from app.pipeline.store import RUN_TYPE_ELIGIBILITY_REQUEST, create_pipeline_run

        pipeline_run_id = create_pipeline_run(
            settings,
            practice_id=practice_id,
            run_type=RUN_TYPE_ELIGIBILITY_REQUEST,
            payload={"request_id": str(serialized["id"])},
            idempotency_key=(
                f"eligibility_pipeline:{serialized['id']}"
                if not payload.get("idempotency_key")
                else f"eligibility_pipeline:{payload['idempotency_key']}"
            ),
        )
        serialized["pipeline_run_id"] = str(pipeline_run_id)
    except Exception as exc:
        logger.warning("eligibility request pipeline enqueue failed: %s", exc)
    return serialized


def list_hitl_tasks(
    settings: Settings,
    *,
    practice_id: str,
    status: str = "pending",
    limit: int = 100,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select *
            from agents.rcm_tasks
            where practice_id = %s
              and status = %s
            order by created_at desc
            limit %s
            """,
            (practice_id, status, limit),
        )
        rows = cur.fetchall()
    return [_serialize_row(dict(row)) for row in rows]


def get_hitl_task(
    settings: Settings,
    *,
    practice_id: str,
    task_id: UUID,
) -> dict[str, Any]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select *
            from agents.rcm_tasks
            where practice_id = %s and id = %s
            limit 1
            """,
            (practice_id, task_id),
        )
        row = cur.fetchone()
    if not row:
        raise DashboardHitlTaskNotFoundError(f"HITL task not found: {task_id}")
    return _serialize_row(dict(row))


def resolve_hitl_task(
    settings: Settings,
    *,
    practice_id: str,
    task_id: UUID,
    action: str,
    performed_by: str,
    final_codes: list[str] | None = None,
    final_summary: str | None = None,
    override_codes: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Approve, reject, or override a pending HITL task."""
    _require_neon(settings)

    if action not in {"approve", "reject", "override"}:
        raise ValueError("invalid_action")

    resolved_status = HITL_STATUS_REJECTED if action == "reject" else HITL_STATUS_APPROVED
    edited_codes = override_codes if action == "override" else final_codes

    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select *
                from agents.rcm_tasks
                where practice_id = %s and id = %s
                for update
                """,
                (practice_id, task_id),
            )
            task_row = cur.fetchone()
            if not task_row:
                raise DashboardHitlTaskNotFoundError(f"HITL task not found: {task_id}")

            task = dict(task_row)
            if str(task.get("status") or "") != HITL_STATUS_PENDING:
                raise DashboardHitlTaskConflictError("task_not_pending")

            ai_codes = task.get("ai_codes") or []
            default_codes = [str(code) for code in ai_codes] if isinstance(ai_codes, list) else []

            codes_for_acceptance = (
                [str(code) for code in edited_codes] if edited_codes else default_codes
            )

            cur.execute(
                """
                update agents.rcm_tasks
                set status = %s,
                    biller_edited_codes = %s,
                    updated_at = now()
                where id = %s and practice_id = %s
                returning *
                """,
                (
                    resolved_status,
                    codes_for_acceptance if action != "reject" else None,
                    task_id,
                    practice_id,
                ),
            )
            updated = cur.fetchone()
            if not updated:
                raise DashboardHitlTaskConflictError("task_update_failed")

            event_type = "task.rejected" if action == "reject" else "task.approved"
            if action == "override":
                event_type = "task.codes_overridden"

            cur.execute(
                """
                insert into agents.rcm_task_events (
                  practice_id, task_id, event_type, actor_label, payload
                )
                values (%s, %s, %s, %s, %s)
                """,
                (
                    practice_id,
                    task_id,
                    event_type,
                    performed_by,
                    Jsonb(
                        {
                            "action": action,
                            "reason": reason,
                            "final_codes": codes_for_acceptance,
                            "override_codes": override_codes,
                        }
                    ),
                ),
            )

            accepted_claim_id: str | None = None
            if action in {"approve", "override"}:
                pipeline_json = task.get("pipeline_json")
                if not isinstance(pipeline_json, dict):
                    pipeline_json = {}

                backend_claim_id = str(task.get("backend_claim_id") or "")
                if not backend_claim_id:
                    claim_draft = pipeline_json.get("claim_draft")
                    if isinstance(claim_draft, dict):
                        backend_claim_id = str(claim_draft.get("id") or "")

                cur.execute(
                    """
                    insert into rcm.accepted_claims (
                      practice_id, task_id, backend_record_id, backend_claim_id,
                      patient_name, payer, final_codes, final_summary, confidence,
                      source_pipeline_json
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        practice_id,
                        task_id,
                        str(task.get("backend_record_id") or ""),
                        backend_claim_id or str(task_id),
                        str(task.get("patient_name") or "Unknown patient"),
                        task.get("payer"),
                        codes_for_acceptance,
                        final_summary or task.get("ai_summary"),
                        task.get("confidence"),
                        Jsonb(pipeline_json),
                    ),
                )
                accepted_row = cur.fetchone()
                accepted_claim_id = str(accepted_row["id"]) if accepted_row else None

                cur.execute(
                    """
                    insert into agents.rcm_task_events (
                      practice_id, task_id, event_type, actor_label, payload
                    )
                    values (%s, %s, %s, %s, %s)
                    """,
                    (
                        practice_id,
                        task_id,
                        "task.claim_accepted",
                        performed_by,
                        Jsonb({"accepted_claim_id": accepted_claim_id}),
                    ),
                )

        conn.commit()

    write_audit_log(
        settings,
        practice_id=practice_id,
        action="hitl.task.resolved",
        entity_type="rcm_task",
        entity_id=task_id,
        performed_by=performed_by,
        metadata={
            "action": action,
            "status": resolved_status,
            "accepted_claim_id": accepted_claim_id,
        },
    )
    record_hitl_resolve_shadow(
        settings,
        practice_id=practice_id,
        task_id=str(task_id),
        action=action,
        ai_codes=default_codes,
        final_codes=codes_for_acceptance if action != "reject" else [],
    )

    return {
        "task_id": str(task_id),
        "status": resolved_status,
        "accepted_claim_id": accepted_claim_id,
        "task": _serialize_row(dict(updated)),
    }


def get_patient_360(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID,
    agent_run_limit: int = 20,
    performed_by: str = "dashboard_bff",
) -> dict[str, Any]:
    _require_neon(settings)
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select *
            from patient.patients
            where practice_id = %s and id = %s
            limit 1
            """,
            (practice_id, patient_id),
        )
        patient = cur.fetchone()
        if not patient:
            raise DashboardPatientNotFoundError(f"Patient not found: {patient_id}")
        cur.execute(
            """
            select *
            from rcm.eligibility_checks
            where practice_id = %s and patient_id = %s
            order by checked_at desc
            limit 1
            """,
            (practice_id, patient_id),
        )
        latest_check = cur.fetchone()
    agent_runs = list_agent_runs_for_patient(
        settings,
        patient_id,
        practice_id=practice_id,
        agent=None,
        limit=agent_run_limit,
    )
    audit_phi_read(
        settings,
        practice_id=practice_id,
        action="phi.patient.read",
        entity_type="patient",
        entity_id=patient_id,
        performed_by=performed_by,
    )
    return {
        "patient": _serialize_row(dict(patient)),
        "latest_eligibility_check": _serialize_row(dict(latest_check)) if latest_check else None,
        "agent_runs": agent_runs,
    }
