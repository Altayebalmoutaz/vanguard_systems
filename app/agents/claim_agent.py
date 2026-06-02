"""
Claim Submission Agent — linear flow (claw-style, no LLM).

1. Gate: if prior auth still requires PA or outstanding document checklist → do not submit.
2. Tool: build_claim_tool → structured claim dict.
3. Tool: submit_claim_tool → mock Stedi response.
"""

from __future__ import annotations

from typing import Literal, cast

from app.schemas.claim import (
    ClaimAgentRequest,
    ClaimDraftResponse,
    ClaimStructure,
    ClaimSubmissionResponse,
)
from app.tools.claim_tools import build_claim_tool, submit_claim_tool


def _claim_details(coding_cdt: list[str], coding_icd: list[str]) -> dict:
    return {
        "cdt_codes": list(coding_cdt),
        "icd10_codes": list(coding_icd),
    }


def _must_hold_for_prior_auth(prior) -> bool:
    """
    Block clearinghouse submission when:
    - payer path requires prior authorization, or
    - there is a non-empty required-documents checklist (office must gather docs / PA first).

    Clear both on the prior_auth object before submitting (e.g. after human workflow).
    """
    if prior.requires_auth:
        return True
    return bool(prior.required_documents)


def run_claim_agent(request: ClaimAgentRequest) -> ClaimSubmissionResponse:
    details = _claim_details(request.coding.cdt_codes, request.coding.icd10_codes)

    if _must_hold_for_prior_auth(request.prior_auth):
        return ClaimSubmissionResponse(
            claim_id="",
            status="pending_auth",
            submission_channel="none",
            details=details,
        )

    claim_dict = build_claim_tool(request)
    submit_result = submit_claim_tool(claim_dict)

    st = cast(Literal["submitted"], submit_result["status"])
    return ClaimSubmissionResponse(
        claim_id=submit_result["claim_id"],
        status=st,
        submission_channel=submit_result["submission_channel"],
        details=details,
    )


def run_claim_draft_agent(request: ClaimAgentRequest) -> ClaimDraftResponse:
    """Build a draft claim for biller review (no submit side-effect)."""
    details = _claim_details(request.coding.cdt_codes, request.coding.icd10_codes)
    if _must_hold_for_prior_auth(request.prior_auth):
        return ClaimDraftResponse(
            status="pending_auth",
            claim_payload={},
            blockers=[
                "Prior authorization and required documentation must be cleared before submit."
            ],
            available_actions=["edit"],
            details=details,
        )

    claim_dict = build_claim_tool(request)
    return ClaimDraftResponse(
        status="draft",
        claim_payload=claim_dict,
        blockers=[],
        available_actions=["edit", "submit"],
        details=details,
    )


def submit_reviewed_claim(payload: dict) -> ClaimSubmissionResponse:
    """Submit biller-reviewed draft payload to clearinghouse adapter."""
    claim = ClaimStructure.model_validate(payload)
    submit_result = submit_claim_tool(claim.model_dump())
    st = cast(Literal["submitted"], submit_result["status"])
    return ClaimSubmissionResponse(
        claim_id=submit_result["claim_id"],
        status=st,
        submission_channel=submit_result["submission_channel"],
        details={
            "cdt_codes": list(claim.codes.cdt),
            "icd10_codes": list(claim.codes.icd10),
        },
    )
