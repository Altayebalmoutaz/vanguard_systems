"""Layer 6 — Explicit routing states (match/case, auditable)."""

from __future__ import annotations

from typing import Any, Literal

from app.eligibility.db import payer_requires_prior_auth
from supabase import Client

ROUTING_POLICY_VERSION = "2.4"

COVERAGE_AMBIGUOUS_ROUTING_REASON = (
    "Coverage could not be confirmed with sufficient confidence from normalized 271 data "
    "(missing aggregate coverage, low confidence label, or ambiguous procedure rows)."
)


def _attach_payer_aaa_errors(detail: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    """Surface Stedi/payer AAA errors on routing detail for billers (no raw JSON dig required)."""
    out = dict(detail)
    aaa = canonical.get("payer_aaa_errors")
    if isinstance(aaa, list) and aaa:
        out["payer_aaa_errors"] = aaa
    aaa_actions = canonical.get("stedi_aaa_actions")
    if isinstance(aaa_actions, list) and aaa_actions:
        out["stedi_aaa_actions"] = aaa_actions
    warnings = canonical.get("stedi_warnings")
    if isinstance(warnings, list) and warnings:
        out["stedi_warnings"] = warnings
    x12_kind = canonical.get("stedi_x12_transaction_kind")
    if isinstance(x12_kind, str) and x12_kind.strip():
        out["stedi_x12_transaction_kind"] = x12_kind.strip()
    return out


def _primary_stedi_action(canonical: dict[str, Any]) -> str | None:
    actions = canonical.get("stedi_aaa_actions")
    if not isinstance(actions, list):
        return None
    priority = [
        "retry_connectivity",
        "enrollment_or_portal_credentials",
        "verify_subscriber",
        "fix_input",
        "human_review",
    ]
    found = {a.get("action") for a in actions if isinstance(a, dict)}
    for action in priority:
        if action in found:
            return action
    return None


def _benefit_blocks_count(canonical: dict[str, Any]) -> int:
    """Count of EB benefit rows in stored raw 271 (0 = payer returned no benefit blocks)."""
    raw = canonical.get("raw_response")
    if not isinstance(raw, dict):
        return 0
    bi = raw.get("benefitsInformation")
    if isinstance(bi, list):
        return len(bi)
    return 0


def _normalized_coverage_confidence(canonical: dict[str, Any]) -> str | None:
    cc = canonical.get("coverage_confidence")
    if not isinstance(cc, str):
        return None
    s = cc.strip().lower()
    return s if s in ("high", "medium", "low") else None


def _routing_state(
    canonical: dict[str, Any],
) -> Literal["INACTIVE", "INCOMPLETE", "NOT_COVERED", "CLEARED", "COVERAGE_AMBIGUOUS"]:
    """
    Priority (first match wins):
    1 INACTIVE — subscriber not active
    2 NOT_COVERED — explicit not-covered at aggregate or procedure level
    3 INCOMPLETE — completeness failed and payer sent no benefit rows at all
    4 COVERAGE_AMBIGUOUS — aggregate coverage unknown, or LLM confidence low
    5 CLEARED — active, complete response, covered with high/medium confidence (or legacy unset confidence)
    """
    is_active = canonical.get("is_active")
    response_complete = bool(canonical.get("response_complete"))
    proc_details = list(canonical.get("procedure_details") or [])
    any_proc_false = any(p.get("procedure_covered") is False for p in proc_details)
    any_proc_true = any(p.get("procedure_covered") is True for p in proc_details)
    is_covered = canonical.get("is_covered")
    cc = _normalized_coverage_confidence(canonical)
    missing = list(canonical.get("missing_fields") or [])
    bc = _benefit_blocks_count(canonical)

    if is_active is False:
        return "INACTIVE"
    # Raw interchange may be a 999 (implementation acknowledgment), not a 271 — do not treat as cleared eligibility.
    if canonical.get("stedi_x12_transaction_kind") == "999":
        return "INCOMPLETE"
    # Explicit not covered from normalized 271 (high confidence in source denial / N / not-covered rows)
    if is_active is True and (is_covered is False or any_proc_false):
        return "NOT_COVERED"
    # No EB rows at all + Layer 4 incomplete → true data gap / bad payload
    if not response_complete and len(missing) > 0 and bc == 0:
        return "INCOMPLETE"
    # Low LLM label, or aggregate coverage still unknown after enrichment
    if is_active is True and (cc == "low" or is_covered is None):
        return "COVERAGE_AMBIGUOUS"
    # Cleared: complete, covered; confidence high/medium or absent (pre-LLM pipelines)
    if (
        is_active is True
        and response_complete
        and (is_covered is True or (any_proc_true and not any_proc_false))
        and (cc is None or cc in ("high", "medium"))
    ):
        return "CLEARED"
    return "INCOMPLETE"


def route(canonical: dict[str, Any], supabase: Client) -> dict[str, Any]:
    """
    Mutually exclusive routing states (no dict-based routing).
    """
    proc_details = list(canonical.get("procedure_details") or [])
    payer_id = str(canonical.get("payer_id") or "").strip().upper()
    codes = sorted(
        {str(p.get("cdt_code")).strip().upper() for p in proc_details if p.get("cdt_code")}
    )

    state = _routing_state(canonical)
    reasons = [f"routing_state:{state}", f"routing_policy_version:{ROUTING_POLICY_VERSION}"]

    match state:
        case "INACTIVE":
            reasons.append("member_inactive")
            return {
                "status": "INACTIVE",
                "action": "notify_front_office_inactive",
                "next_agent": None,
                "notify_front_office": True,
                "detail": _attach_payer_aaa_errors(
                    {
                        "inactive_reason": canonical.get("inactive_reason"),
                        "message": "Do not route to downstream agents",
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": reasons,
                    },
                    canonical,
                ),
            }
        case "INCOMPLETE":
            reasons.append("completeness_gate_failed")
            if canonical.get("stedi_x12_transaction_kind") == "999":
                reasons.append("stedi_x12_999_implementation_ack")
            if canonical.get("payer_aaa_errors"):
                reasons.append("payer_aaa_errors_present")
            aaa_action = _primary_stedi_action(canonical)
            if aaa_action:
                reasons.append(f"stedi_aaa_action:{aaa_action}")
            incomplete_msg = "Manual correction then scheduled re-check; no immediate auto-retry"
            if aaa_action == "retry_connectivity":
                incomplete_msg = "Payer connectivity issue persisted after automatic retries; retry later or escalate if payer-wide."
            elif aaa_action == "enrollment_or_portal_credentials":
                incomplete_msg = "Provider enrollment or payer portal PIN/password is required before retrying eligibility."
            elif aaa_action == "verify_subscriber":
                incomplete_msg = "Verify member ID, legal name, date of birth, and payer before retrying eligibility."
            elif canonical.get("payer_aaa_errors"):
                incomplete_msg = "Payer returned AAA error(s). Verify member ID, patient name, date of birth, and payer id, then retry."
            return {
                "status": "INCOMPLETE",
                "action": "notify_front_office_missing_fields",
                "next_agent": None,
                "notify_front_office": True,
                "detail": _attach_payer_aaa_errors(
                    {
                        "missing_fields": list(canonical.get("missing_fields") or []),
                        "integrity_warnings": list(canonical.get("integrity_warnings") or []),
                        "message": incomplete_msg,
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": reasons,
                    },
                    canonical,
                ),
            }
        case "NOT_COVERED":
            reasons.append("coverage_or_procedure_not_covered")
            return {
                "status": "NOT_COVERED",
                "action": "patient_financial_agreement_required",
                "next_agent": None,
                "notify_front_office": True,
                "detail": _attach_payer_aaa_errors(
                    {
                        "message": "Patient financial agreement required before treatment",
                        "procedure_details": proc_details,
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": reasons,
                    },
                    canonical,
                ),
            }
        case "COVERAGE_AMBIGUOUS":
            ncc = _normalized_coverage_confidence(canonical)
            if ncc == "low":
                reasons.append("coverage_confidence_low")
            elif canonical.get("is_covered") is None:
                reasons.append("aggregate_coverage_unknown")
            else:
                reasons.append("coverage_ambiguous")
            cp = canonical.get("copay")
            try:
                copay_part = (
                    f"Copay of ${float(cp):.0f} confirmed if covered."
                    if cp is not None
                    else "Verify coinsurance and copay with payer if covered."
                )
            except (TypeError, ValueError):
                copay_part = "Verify coinsurance and copay with payer if covered."
            suggested = f"Call payer to verify procedure coverage. {copay_part}"
            return {
                "status": "COVERAGE_AMBIGUOUS",
                "routing_reason": COVERAGE_AMBIGUOUS_ROUTING_REASON,
                "suggested_action": suggested,
                "action": "notify_front_office_coverage_ambiguous",
                "next_agent": None,
                "notify_front_office": True,
                "detail": _attach_payer_aaa_errors(
                    {
                        "message": suggested,
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": reasons,
                    },
                    canonical,
                ),
            }
        case "CLEARED":
            needs_auth = (
                payer_requires_prior_auth(supabase, payer_id, codes)
                if payer_id and codes
                else False
            )
            if needs_auth:
                reasons.append("prior_auth_required_by_rule")
                return {
                    "status": "CLEARED",
                    "action": "route_prior_auth",
                    "next_agent": "prior_auth",
                    "notify_front_office": False,
                    "detail": _attach_payer_aaa_errors(
                        {
                            "payer_id": payer_id,
                            "cdt_codes": codes,
                            "routing_policy_version": ROUTING_POLICY_VERSION,
                            "reasons": reasons,
                        },
                        canonical,
                    ),
                }
            reasons.append("cleared_without_prior_auth_rule")
            return {
                "status": "CLEARED",
                "action": "route_coding",
                "next_agent": "coding",
                "notify_front_office": False,
                "detail": _attach_payer_aaa_errors(
                    {
                        "payer_id": payer_id,
                        "cdt_codes": codes,
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": reasons,
                    },
                    canonical,
                ),
            }
        case _:
            return {
                "status": "INCOMPLETE",
                "action": "unclassified_state",
                "next_agent": None,
                "notify_front_office": True,
                "detail": _attach_payer_aaa_errors(
                    {
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                        "reasons": [
                            "routing_state:UNCLASSIFIED",
                            f"routing_policy_version:{ROUTING_POLICY_VERSION}",
                        ],
                    },
                    canonical,
                ),
            }
