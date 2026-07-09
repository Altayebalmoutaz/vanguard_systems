"""Neon-backed read helpers for RCM module BFF routes (Wave 6)."""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from app.config import Settings
from app.dashboard.store import _serialize_value
from app.db.connection import NeonNotConfiguredError, get_neon_dsn, neon_connection

_MODULE_HREF = {
    "Coding": "/coding",
    "Prior Auth": "/prior-auth",
    "Claims": "/claims",
    "Denials": "/denials",
}


# region agent log
def _agent_debug_log(hypothesis_id: str, message: str, data: dict[str, Any]) -> None:
    try:
        with open("debug-c16f79.log", "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": "c16f79",
                        "runId": "initial",
                        "hypothesisId": hypothesis_id,
                        "location": "app/dashboard/rcm_store.py",
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
# endregion


def _require_neon(settings: Settings) -> None:
    if not get_neon_dsn(settings):
        raise NeonNotConfiguredError("NEON_DATABASE_URL is not configured")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _as_str_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item).strip()]


def _parse_amount(value: Any) -> float:
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if not value:
        return 0.0
    cleaned = re.sub(r"[^0-9.]", "", str(value))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _split_lines(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _shape_coding_decision(row: dict[str, Any]) -> dict[str, Any]:
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    payer_rules = output.get("payer_rules_matched")
    if isinstance(payer_rules, list):
        rules_matched = [
            item if isinstance(item, dict) else {"rule": str(item), "detail": ""}
            for item in payer_rules
        ]
    else:
        rules_matched = []

    try:
        confidence = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "id": str(row.get("id") or ""),
        "source_type": "agent_decision",
        "decision_id": str(row.get("id") or ""),
        "encounter_id": str(row.get("encounter_id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": _serialize_value(row.get("dob")) or "",
        "provider_name": str(row.get("provider_name") or row.get("provider_id") or "Unknown"),
        "payer": str(row.get("payer") or "Unknown payer"),
        "clinical_note": str(row.get("clinical_note") or ""),
        "cdt_codes": _as_str_list(output.get("cdt_codes")),
        "icd10_codes": _as_str_list(output.get("icd10_codes")),
        "confidence": confidence,
        "justification": str(row.get("reasoning") or ""),
        "payer_flags": _as_str_list(output.get("payer_flags")),
        "payer_rules_matched": rules_matched,
        "status": str(row.get("status") or "pending_review"),
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def _shape_coding_task(row: dict[str, Any]) -> dict[str, Any]:
    pipeline = row.get("pipeline_json") if isinstance(row.get("pipeline_json"), dict) else {}
    coding = pipeline.get("coding") if isinstance(pipeline.get("coding"), dict) else {}
    output_rules = coding.get("payer_rules_matched")
    if isinstance(output_rules, list):
        rules_matched = [
            item if isinstance(item, dict) else {"rule": str(item), "detail": ""}
            for item in output_rules
        ]
    else:
        rules_matched = []

    try:
        confidence = float(row.get("confidence") or coding.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "id": str(row.get("id") or ""),
        "source_type": "rcm_task",
        "hitl_task_id": str(row.get("id") or ""),
        "decision_id": str(row.get("backend_record_id") or pipeline.get("decision_id") or ""),
        "encounter_id": str(pipeline.get("encounter_id") or row.get("backend_record_id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": str(row.get("patient_dob") or ""),
        "provider_name": "Unknown",
        "payer": str(row.get("payer") or "Unknown payer"),
        "clinical_note": str(row.get("clinical_note") or ""),
        "cdt_codes": _as_str_list(row.get("ai_codes") or coding.get("cdt_codes")),
        "icd10_codes": _as_str_list(coding.get("icd10_codes")),
        "confidence": confidence,
        "justification": str(row.get("ai_summary") or coding.get("justification") or ""),
        "payer_flags": _as_str_list(coding.get("payer_flags")),
        "payer_rules_matched": rules_matched,
        "status": "pending_review",
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def list_coding_cases(
    settings: Settings,
    *,
    practice_id: str,
    status: str | None = None,
    limit: int = 75,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    status_filter = status or None

    decisions_sql = """
        select
          d.id, d.encounter_id, d.reasoning, d.output, d.confidence, d.status, d.created_at,
          e.clinical_note, e.provider_id, pr.full_name as provider_name,
          p.name as patient_name, p.dob, p.payer
        from agents.agent_decisions d
        join patient.encounters e on e.id = d.encounter_id and e.practice_id = d.practice_id
        join patient.patients p on p.id = e.patient_id and p.practice_id = d.practice_id
        left join patient.providers pr on pr.id = e.provider_id and pr.practice_id = d.practice_id
        where d.practice_id = %s and (%s::text is null or d.status = %s::text)
        order by d.created_at desc
        limit %s
    """
    tasks_sql = """
        select * from agents.rcm_tasks
        where practice_id = %s and status = 'pending'
          and task_type in ('Coding review', 'Full RCM pipeline')
        order by created_at desc
        limit %s
    """

    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(decisions_sql, (practice_id, status_filter, status_filter, limit))
            decision_rows = cur.fetchall()
            cur.execute(tasks_sql, (practice_id, limit))
            task_rows = cur.fetchall()

    cases = [_shape_coding_decision(dict(row)) for row in decision_rows]
    seen = {case["id"] for case in cases}
    for row in task_rows:
        shaped = _shape_coding_task(dict(row))
        if shaped["id"] not in seen:
            cases.append(shaped)
            seen.add(shaped["id"])
    return cases[:limit]


def _shape_prior_auth_run(row: dict[str, Any]) -> dict[str, Any]:
    output = row.get("output_json") if isinstance(row.get("output_json"), dict) else {}
    input_json = row.get("input_json") if isinstance(row.get("input_json"), dict) else {}
    coding = input_json.get("coding") if isinstance(input_json.get("coding"), dict) else {}
    cdt_codes = _as_str_list(coding.get("cdt_codes"))
    procedure = cdt_codes[0] if cdt_codes else "Unknown"
    status = str(row.get("status") or "pending_review")
    ui_status = status if status in {"pending_review", "approved"} else "pending_review"
    if status == "approved" and not output.get("requires_auth"):
        ui_status = "submitted"
    risk_level = str(output.get("risk_level") or "medium")
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
    return {
        "id": str(row.get("id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": _serialize_value(row.get("dob")) or "",
        "procedure": procedure,
        "procedure_label": procedure,
        "payer": str(row.get("payer_id") or input_json.get("insurance") or "Unknown payer"),
        "requires_auth": bool(output.get("requires_auth")),
        "required_documents": _as_str_list(output.get("required_documents")),
        "payer_rules": _as_str_list(output.get("payer_rules")),
        "risk_level": risk_level,
        "risk_reason": str(output.get("risk_reason") or ""),
        "status": ui_status,
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def _shape_prior_auth_task(row: dict[str, Any]) -> dict[str, Any]:
    pipeline = row.get("pipeline_json") if isinstance(row.get("pipeline_json"), dict) else {}
    prior = pipeline.get("prior_auth") if isinstance(pipeline.get("prior_auth"), dict) else {}
    coding = pipeline.get("coding") if isinstance(pipeline.get("coding"), dict) else {}
    cdt_codes = _as_str_list(coding.get("cdt_codes") or row.get("ai_codes"))
    procedure = cdt_codes[0] if cdt_codes else "Unknown"
    risk_level = str(prior.get("risk_level") or "medium")
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
    return {
        "id": str(row.get("id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": str(row.get("patient_dob") or ""),
        "procedure": procedure,
        "procedure_label": procedure,
        "payer": str(row.get("payer") or "Unknown payer"),
        "requires_auth": bool(prior.get("requires_auth")),
        "required_documents": _as_str_list(prior.get("required_documents")),
        "payer_rules": _as_str_list(prior.get("payer_rules")),
        "risk_level": risk_level,
        "risk_reason": str(prior.get("risk_reason") or row.get("ai_summary") or ""),
        "status": "pending_review",
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def list_prior_auth_cases(
    settings: Settings,
    *,
    practice_id: str,
    status: str | None = None,
    limit: int = 75,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    status_filter = status if status in {"pending_review", "approved", "denied", "expired", "superseded"} else None
    runs_sql = """
        select ar.id, ar.patient_id, ar.payer_id, ar.status, ar.input_json, ar.output_json, ar.created_at,
               p.name as patient_name, p.dob
        from rcm.agent_runs ar
        left join patient.patients p on p.id = ar.patient_id and p.practice_id = ar.practice_id
        where ar.practice_id = %s and ar.agent = 'prior_auth'
          and (%s::text is null or ar.status = %s::text)
        order by ar.created_at desc
        limit %s
    """
    tasks_sql = """
        select * from agents.rcm_tasks
        where practice_id = %s and status = 'pending' and task_type = 'Prior auth review'
        order by created_at desc
        limit %s
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(runs_sql, (practice_id, status_filter, status_filter, limit))
            run_rows = cur.fetchall()
            cur.execute(tasks_sql, (practice_id, limit))
            task_rows = cur.fetchall()
    cases = [_shape_prior_auth_run(dict(row)) for row in run_rows]
    seen = {case["id"] for case in cases}
    for row in task_rows:
        shaped = _shape_prior_auth_task(dict(row))
        if shaped["id"] not in seen:
            cases.append(shaped)
            seen.add(shaped["id"])
    return cases[:limit]


def _shape_claim_row(row: dict[str, Any]) -> dict[str, Any]:
    cdt_lines = row.get("cdt_lines") if isinstance(row.get("cdt_lines"), dict) else {}
    service_lines_raw = _as_list(cdt_lines.get("service_lines"))
    service_lines: list[dict[str, Any]] = []
    total = 0.0
    for line in service_lines_raw:
        if not isinstance(line, dict):
            continue
        amount = _parse_amount(line.get("charge_amount"))
        total += amount
        service_lines.append(
            {
                "cdt_code": str(line.get("cdt_code") or line.get("cdt") or ""),
                "description": str(line.get("description") or line.get("cdt_code") or "Service"),
                "charge_amount": amount,
            }
        )
    status = str(row.get("status") or "draft")
    if status not in {"draft", "pending_auth", "submitted", "paid"}:
        status = "draft"
    blockers = _as_str_list(row.get("compliance_flags"))
    submission_channel = "stedi" if status in {"submitted", "paid"} else "none"
    return {
        "claim_id": str(row.get("id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": _serialize_value(row.get("dob")) or "",
        "payer": str(row.get("payer") or "Unknown payer"),
        "provider_name": str(row.get("provider") or "Unknown provider"),
        "status": status,
        "submission_channel": submission_channel,
        "diagnosis_codes": _as_str_list(row.get("icd10_codes")),
        "service_lines": service_lines,
        "total_charge_amount": total,
        "blockers": blockers,
        "available_actions": ["edit"] if blockers else ["edit", "submit"],
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def list_claim_cases(
    settings: Settings,
    *,
    practice_id: str,
    status: str | None = None,
    limit: int = 75,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    sql = """
        select c.id, c.patient_id, c.provider, c.status, c.cdt_lines, c.icd10_codes,
               c.compliance_flags, c.created_at, p.name as patient_name, p.dob, p.payer
        from rcm.claims c
        left join patient.patients p on p.id = c.patient_id and p.practice_id = c.practice_id
        where c.practice_id = %s and (%s::text is null or c.status = %s::text)
        order by c.created_at desc
        limit %s
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (practice_id, status, status, limit))
            rows = cur.fetchall()
    return [_shape_claim_row(dict(row)) for row in rows]


def _shape_denial_row(row: dict[str, Any]) -> dict[str, Any]:
    db_status = str(row.get("status") or "pending")
    steps = _split_lines(row.get("corrective_actions"))
    return {
        "claim_id": str(row.get("claim_reference") or row.get("id") or ""),
        "patient_name": str(row.get("patient_name") or row.get("claim_reference") or "Unknown patient"),
        "dob": _serialize_value(row.get("dob")) or "",
        "payer": str(row.get("payer") or "Unknown payer"),
        "status": "paid" if db_status == "resolved" else ("partial" if db_status == "in_appeal" else "denied"),
        "reason": str(row.get("provider_code") or "unknown"),
        "reason_label": str(row.get("root_cause") or row.get("executive_summary") or "Denial"),
        "next_action": steps[0] if steps else "review_denial",
        "amount_at_risk": _parse_amount(row.get("recoverable_amount")),
        "resubmission_steps": steps,
        "required_evidence": _split_lines(row.get("missing_documents")),
        "reasoning_summary": str(row.get("executive_summary") or row.get("validity_reasoning") or ""),
        "appeal_letter": str(row.get("coding_note") or ""),
        "requires_human_review": db_status in {"pending", "in_appeal"},
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def _shape_denial_task(row: dict[str, Any]) -> dict[str, Any]:
    pipeline = row.get("pipeline_json") if isinstance(row.get("pipeline_json"), dict) else {}
    denial = pipeline.get("denial") if isinstance(pipeline.get("denial"), dict) else {}
    return {
        "claim_id": str(denial.get("claim_id") or row.get("backend_claim_id") or row.get("id") or ""),
        "patient_name": str(row.get("patient_name") or "Unknown patient"),
        "dob": str(row.get("patient_dob") or ""),
        "payer": str(row.get("payer") or "Unknown payer"),
        "status": str(denial.get("status") or "denied"),
        "reason": str(denial.get("reason") or denial.get("deterministic_reason_token") or "unknown"),
        "reason_label": str(denial.get("reasoning_summary") or denial.get("reason") or "Denial review"),
        "next_action": str(denial.get("next_action") or "review_denial"),
        "amount_at_risk": 0.0,
        "resubmission_steps": _as_str_list(denial.get("resubmission_steps")),
        "required_evidence": _as_str_list(denial.get("required_evidence")),
        "reasoning_summary": str(denial.get("reasoning_summary") or row.get("ai_summary") or ""),
        "appeal_letter": str(denial.get("appeal_letter") or ""),
        "requires_human_review": bool(denial.get("requires_human_review", True)),
        "created_at": _serialize_value(row.get("created_at")) or "",
    }


def list_denial_cases(
    settings: Settings,
    *,
    practice_id: str,
    status: str | None = None,
    limit: int = 75,
) -> list[dict[str, Any]]:
    _require_neon(settings)
    db_status = {"denied": "pending", "partial": "in_appeal", "paid": "resolved"}.get(status or "", None)
    sql = """
        select dc.id, dc.claim_reference, dc.payer, dc.provider_code, dc.root_cause,
               dc.corrective_actions, dc.missing_documents, dc.recoverable_amount,
               dc.executive_summary, dc.validity_reasoning, dc.coding_note, dc.status, dc.created_at,
               p.name as patient_name, p.dob
        from rcm.denied_claims dc
        left join rcm.claims c on c.id::text = dc.claim_reference and c.practice_id = dc.practice_id
        left join patient.patients p on p.id = c.patient_id and p.practice_id = dc.practice_id
        where dc.practice_id = %s and (%s::text is null or dc.status = %s::text)
        order by dc.created_at desc
        limit %s
    """
    tasks_sql = """
        select * from agents.rcm_tasks
        where practice_id = %s and status = 'pending' and task_type = 'Denial review'
        order by created_at desc
        limit %s
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (practice_id, db_status, db_status, limit))
            denial_rows = cur.fetchall()
            cur.execute(tasks_sql, (practice_id, limit))
            task_rows = cur.fetchall()
    cases = [_shape_denial_row(dict(row)) for row in denial_rows]
    seen = {case["claim_id"] for case in cases}
    for row in task_rows:
        shaped = _shape_denial_task(dict(row))
        if shaped["claim_id"] not in seen:
            cases.append(shaped)
            seen.add(shaped["claim_id"])
    return cases[:limit]


def list_dashboard_worklist(
    settings: Settings,
    *,
    practice_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case in list_coding_cases(settings, practice_id=practice_id, status="pending_review", limit=10):
        conf = float(case.get("confidence") or 0)
        items.append(
            {
                "id": case["id"],
                "module": "Coding",
                "patient_name": case["patient_name"],
                "payer": case["payer"],
                "summary": case["justification"] or "Coding review pending",
                "amount": 0.0,
                "severity": "high" if conf < 0.7 else ("medium" if conf < 0.85 else "low"),
                "href": _MODULE_HREF["Coding"],
            }
        )
    for case in list_prior_auth_cases(settings, practice_id=practice_id, status="pending_review", limit=10):
        items.append(
            {
                "id": case["id"],
                "module": "Prior Auth",
                "patient_name": case["patient_name"],
                "payer": case["payer"],
                "summary": case["risk_reason"] or "Prior auth review pending",
                "amount": 0.0,
                "severity": case.get("risk_level") or "medium",
                "href": _MODULE_HREF["Prior Auth"],
            }
        )
    for case in list_claim_cases(settings, practice_id=practice_id, limit=10):
        if case["status"] not in {"draft", "pending_auth"}:
            continue
        items.append(
            {
                "id": case["claim_id"],
                "module": "Claims",
                "patient_name": case["patient_name"],
                "payer": case["payer"],
                "summary": f"Claim {case['status']}",
                "amount": case["total_charge_amount"],
                "severity": "high" if case["status"] == "pending_auth" else "medium",
                "href": _MODULE_HREF["Claims"],
            }
        )
    for case in list_denial_cases(settings, practice_id=practice_id, limit=10):
        if case["status"] == "paid":
            continue
        items.append(
            {
                "id": case["claim_id"],
                "module": "Denials",
                "patient_name": case["patient_name"],
                "payer": case["payer"],
                "summary": case["reason_label"],
                "amount": case["amount_at_risk"],
                "severity": "high" if case["requires_human_review"] else "medium",
                "href": _MODULE_HREF["Denials"],
            }
        )
    severity_rank = {"high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda item: severity_rank.get(str(item.get("severity")), 3))
    return items[:limit]


def get_dashboard_analytics(
    settings: Settings,
    *,
    practice_id: str,
) -> dict[str, Any]:
    _require_neon(settings)
    payer_sql = """
        select coalesce(p.payer, 'Unknown') as payer, count(*) as claim_count
        from rcm.claims c
        left join patient.patients p on p.id = c.patient_id and p.practice_id = c.practice_id
        where c.practice_id = %s and c.created_at >= date_trunc('month', now())
        group by 1 order by 2 desc limit 6
    """
    throughput_sql = """
        select day::date as day, sum(actions) as actions from (
          select date_trunc('day', created_at) as day, count(*) as actions
          from rcm.agent_runs where practice_id = %s and created_at >= now() - interval '7 days'
          group by 1
          union all
          select date_trunc('day', created_at) as day, count(*) as actions
          from agents.agent_decisions where practice_id = %s and created_at >= now() - interval '7 days'
          group by 1
        ) t group by 1 order by 1
    """
    monthly_sql = """
        select date_trunc('month', created_at) as month,
               count(*) filter (where status = 'submitted') as submitted,
               count(*) filter (where status = 'submitted'
                 and (compliance_flags is null or compliance_flags = '[]'::jsonb)) as clean
        from rcm.claims
        where practice_id = %s and created_at >= now() - interval '12 months'
        group by 1 order by 1
    """
    denial_monthly_sql = """
        select date_trunc('month', created_at) as month, count(*) as denials
        from rcm.denied_claims
        where practice_id = %s and created_at >= now() - interval '12 months'
        group by 1 order by 1
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(payer_sql, (practice_id,))
            payer_rows = cur.fetchall()
            cur.execute(throughput_sql, (practice_id, practice_id))
            throughput_rows = cur.fetchall()
            cur.execute(monthly_sql, (practice_id,))
            monthly_rows = cur.fetchall()
            cur.execute(denial_monthly_sql, (practice_id,))
            denial_rows = cur.fetchall()

    payer_colors = ["#005DAA", "#00A88E", "#003594", "#0090DA", "#002677", "#94a3b8"]
    payer_mix = [
        {
            "label": str(row.get("payer") or "Unknown"),
            "value": int(row.get("claim_count") or 0),
            "color": payer_colors[idx % len(payer_colors)],
        }
        for idx, row in enumerate(payer_rows)
    ]
    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    agent_throughput = [
        {"label": weekday_labels[idx % 7], "value": int(row.get("actions") or 0)}
        for idx, row in enumerate(throughput_rows)
    ]
    denial_by_month = {row["month"]: int(row.get("denials") or 0) for row in denial_rows if row.get("month")}
    monthly_trend: list[float] = []
    denial_trend: list[float] = []
    for row in monthly_rows:
        submitted = int(row.get("submitted") or 0)
        clean = int(row.get("clean") or 0)
        month = row.get("month")
        monthly_trend.append(round((clean / submitted) * 100, 1) if submitted else 0.0)
        denials = denial_by_month.get(month, 0)
        denial_trend.append(round((denials / max(submitted + denials, 1)) * 100, 1))
    while len(monthly_trend) < 12:
        monthly_trend.insert(0, monthly_trend[0] if monthly_trend else 0.0)
        denial_trend.insert(0, denial_trend[0] if denial_trend else 0.0)
    monthly_trend = monthly_trend[-12:]
    denial_trend = denial_trend[-12:]
    total_actions = sum(item["value"] for item in agent_throughput)
    return {
        "practice_id": practice_id,
        "payer_mix": payer_mix,
        "agent_throughput": agent_throughput,
        "monthly_trend": monthly_trend,
        "denial_trend": denial_trend,
        "kpis": {
            "ai_actions_per_day": round(total_actions / max(len(agent_throughput), 1)),
            "first_pass_yield": monthly_trend[-1] if monthly_trend else 0.0,
            "claims_this_month": sum(item["value"] for item in payer_mix),
        },
    }


def get_dashboard_overview(
    settings: Settings,
    *,
    practice_id: str,
) -> dict[str, Any]:
    _require_neon(settings)
    started = time.perf_counter()
    _agent_debug_log("H10,H11", "overview store start", {"practiceId": practice_id})
    counts_sql = """
        select
          (select count(*) from rcm.eligibility_requests er
            where er.practice_id = %s and er.status in ('completed', 'needs_attention')
              and er.completed_at >= date_trunc('day', now())) as eligibility_today,
          (select count(*) from agents.agent_decisions d
            where d.practice_id = %s and d.status = 'pending_review') as coding_pending,
          (select count(*) from rcm.claims c
            where c.practice_id = %s and c.status in ('draft', 'pending_auth')) as claims_open,
          (select count(*) from rcm.denied_claims dc
            where dc.practice_id = %s and dc.status in ('pending', 'in_appeal')) as denials_open,
          (select count(*) from rcm.claims c
            where c.practice_id = %s and c.status = 'submitted'
              and c.created_at >= now() - interval '30 days') as claims_submitted_30d,
          (select count(*) from rcm.denied_claims dc
            where dc.practice_id = %s and dc.created_at >= now() - interval '30 days') as denials_30d
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(counts_sql, (practice_id,) * 6)
            counts = dict(cur.fetchone() or {})
    after_counts = time.perf_counter()
    _agent_debug_log(
        "H10,H11",
        "overview counts complete",
        {"practiceId": practice_id, "elapsedMs": round((after_counts - started) * 1000)},
    )

    submitted = int(counts.get("claims_submitted_30d") or 0)
    denials = int(counts.get("denials_30d") or 0)
    denial_rate = round((denials / max(submitted + denials, 1)) * 100, 1)
    clean_claim_rate = round(max(0.0, 100.0 - denial_rate), 1)
    analytics = get_dashboard_analytics(settings, practice_id=practice_id)
    after_analytics = time.perf_counter()
    _agent_debug_log(
        "H10,H11",
        "overview analytics complete",
        {
            "practiceId": practice_id,
            "stepElapsedMs": round((after_analytics - after_counts) * 1000),
            "totalElapsedMs": round((after_analytics - started) * 1000),
        },
    )
    worklist = list_dashboard_worklist(settings, practice_id=practice_id)
    after_worklist = time.perf_counter()
    _agent_debug_log(
        "H10,H11",
        "overview worklist complete",
        {
            "practiceId": practice_id,
            "stepElapsedMs": round((after_worklist - after_analytics) * 1000),
            "totalElapsedMs": round((after_worklist - started) * 1000),
            "worklistCount": len(worklist),
        },
    )
    return {
        "practice_id": practice_id,
        "worklist": worklist,
        "revenue_funnel": [
            {"label": "Eligibility verified", "count": int(counts.get("eligibility_today") or 0), "value": 0},
            {"label": "Coding pending", "count": int(counts.get("coding_pending") or 0), "value": 0},
            {"label": "Claims open", "count": int(counts.get("claims_open") or 0), "value": 0},
            {"label": "Claims submitted (30d)", "count": submitted, "value": 0},
        ],
        "monthly_trend": analytics.get("monthly_trend") or [clean_claim_rate] * 12,
        "denial_trend": analytics.get("denial_trend") or [denial_rate] * 12,
        "kpis": {
            "clean_claim_rate": clean_claim_rate,
            "denial_rate": denial_rate,
            "eligibility_verified_today": int(counts.get("eligibility_today") or 0),
            "coding_pending": int(counts.get("coding_pending") or 0),
            "claims_open": int(counts.get("claims_open") or 0),
            "denials_open": int(counts.get("denials_open") or 0),
            "revenue_at_risk": sum(float(item.get("amount") or 0) for item in worklist),
        },
    }
