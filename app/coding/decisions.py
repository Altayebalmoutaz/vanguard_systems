"""Record dentist approve/edit/reject decisions — the coding agent's ground truth."""

from __future__ import annotations

import logging
from typing import Any

from app.audit.writer import write_audit_log
from app.coding.config import CodingSettings, get_coding_settings
from app.coding.schemas import CodingDecisionRequest, CodingDecisionResponse
from app.coding.store import fetch_run_by_id, insert_coding_decisions
from app.config import Settings, get_settings
from app.observability.metrics import inc
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)


def _suggested_by_line(response_payload: Any) -> dict[str, str | None]:
    """Map line_id -> suggested cdt_code from a stored suggest response."""
    out: dict[str, str | None] = {}
    if not isinstance(response_payload, dict):
        return out
    for rec in response_payload.get("recommendations") or []:
        if isinstance(rec, dict) and rec.get("line_id") is not None:
            code = rec.get("cdt_code")
            out[str(rec["line_id"])] = str(code).upper().strip() if code else None
    return out


def run_record_decision(
    request: CodingDecisionRequest,
    *,
    settings: Settings | None = None,
    coding_settings: CodingSettings | None = None,
) -> CodingDecisionResponse:
    """Persist per-line dentist decisions for a prior suggest run."""
    app_settings = settings or get_settings()
    _ = coding_settings or get_coding_settings()
    practice_id = request.practice_id.strip()

    run = fetch_run_by_id(
        app_settings, practice_id=practice_id, coding_run_id=request.coding_run_id
    )
    suggested = _suggested_by_line(run.get("response_payload") if run else None)
    payer_id = str(run.get("payer_id")) if run and run.get("payer_id") else None

    decisions: list[dict[str, Any]] = []
    for d in request.decisions:
        # Trust the client's suggested_cdt, else backfill from the stored run.
        suggested_cdt = d.suggested_cdt or suggested.get(d.line_id)
        decisions.append(
            {
                "line_id": d.line_id,
                "action": d.action.value,
                "suggested_cdt": suggested_cdt,
                "final_cdt": d.final_cdt,
                "edit_reason": d.edit_reason,
            }
        )
        inc("coding_decision_total", {"action": d.action.value})
        # Top-1 signal: approved-unchanged (or edited-to-same) counts as a hit.
        if suggested_cdt is not None:
            hit = d.action.value == "approved" or (
                d.final_cdt is not None and d.final_cdt == suggested_cdt
            )
            inc("coding_decision_top1", {"result": "hit" if hit else "miss"})

    recorded = insert_coding_decisions(
        app_settings,
        practice_id=practice_id,
        coding_run_id=request.coding_run_id,
        request_id=request.request_id,
        decided_by=request.decided_by,
        payer_id=payer_id,
        decisions=decisions,
    )

    try:
        write_audit_log(
            app_settings,
            practice_id=practice_id,
            action="coding.decision",
            entity_type="coding_run",
            entity_id=request.coding_run_id,
            performed_by=request.decided_by or "scribe_partner",
            metadata={
                "coding_run_id": str(request.coding_run_id),
                "recorded": recorded,
                "actions": [d["action"] for d in decisions],
            },
        )
    except Exception as exc:  # audit must never block capture
        logger.warning("coding.decision audit failed: %s", scrub_for_log(str(exc)))

    return CodingDecisionResponse(
        coding_run_id=request.coding_run_id, recorded=recorded, status="recorded"
    )
