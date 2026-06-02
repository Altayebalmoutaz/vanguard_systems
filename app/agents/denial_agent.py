"""
Denial / ERA Agent — linear tool chain (claw-style, no LLM).

1. parse_era_tool(mock ERA / or full input dict with mock_era)
2. detect_denial_reason_tool(parsed era + claim snapshot)
3. map_denial_reason_tool(status + reason) → next_action
4. auto_resubmit_tool(claim_id, next_action) → resubmission_steps
5. generate_appeal_letter_tool(...) → appeal_letter (denied + submitted claims only)

If claim was never submitted (empty claim_id), skip payer ERA semantics and return
operational guidance without an appeal letter.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from app.config import get_settings
from app.schemas.denial import DenialAgentRequest, DenialAgentResponse
from app.tools.denial_tools import (
    auto_resubmit_tool,
    denial_llm_intelligence_tool,
    detect_denial_reason_tool,
    generate_appeal_letter_tool,
    map_denial_reason_tool,
    parse_era_tool,
)


def _claim_snapshot(request: DenialAgentRequest) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "cdt_codes": list(request.cdt_codes),
        "icd10_codes": list(request.icd10_codes),
    }
    if request.patient_name:
        snap["patient_name"] = request.patient_name
    return snap


def run_denial_agent(request: DenialAgentRequest) -> DenialAgentResponse:
    if not (request.claim_id or "").strip():
        next_action = "resolve_prior_auth_then_submit_claim"
        steps = auto_resubmit_tool("", next_action)
        return DenialAgentResponse(
            claim_id="",
            status="denied",
            reason="claim_not_submitted",
            next_action=next_action,
            appeal_letter="",
            resubmission_steps=steps,
        )

    era_dict = request.mock_era.model_dump()
    parsed = parse_era_tool(era_dict)
    claim_snapshot = _claim_snapshot(request)

    llm_reason_token = ""
    llm_confidence = 0.0
    reasoning_summary = ""
    required_evidence: list[str] = []
    try:
        llm_out = denial_llm_intelligence_tool(get_settings(), parsed, claim_snapshot)
        llm_reason_token = str(llm_out.get("reason_token") or "")
        llm_confidence = float(llm_out.get("confidence") or 0.0)
        reasoning_summary = str(llm_out.get("reasoning_summary") or "")
        required_evidence = list(llm_out.get("required_evidence") or [])
    except Exception:
        # Keep deterministic-only behavior when intelligence layer is unavailable.
        pass

    reason_token = detect_denial_reason_tool(parsed, claim_snapshot)
    next_action = map_denial_reason_tool(parsed["status"], reason_token)
    requires_human_review = bool(
        llm_reason_token and llm_reason_token != reason_token and llm_confidence >= 0.7
    )

    display_reason = reason_token if parsed["status"] != "paid" else ""
    st = cast(Literal["paid", "denied", "partial"], parsed["status"])

    steps = auto_resubmit_tool(request.claim_id, next_action)

    appeal = ""
    if st == "denied":
        appeal = generate_appeal_letter_tool(
            {
                "insurance_company_name": request.insurance_company_name,
                "claim_id": request.claim_id,
                "patient_name": request.patient_name or "the patient",
                "reason_token": reason_token,
                "cdt_codes": request.cdt_codes,
                "icd10_codes": request.icd10_codes,
                "provider_name": request.provider_name,
            }
        )

    return DenialAgentResponse(
        claim_id=request.claim_id,
        status=st,
        reason=display_reason,
        next_action=next_action,
        appeal_letter=appeal,
        resubmission_steps=steps,
        llm_reason_token=llm_reason_token,
        deterministic_reason_token=reason_token,
        llm_confidence=llm_confidence,
        reasoning_summary=reasoning_summary,
        required_evidence=required_evidence,
        requires_human_review=requires_human_review,
    )
