"""Voice escalation eligibility gates (Layer 6.5)."""

from __future__ import annotations

from typing import Any

VOICE_ESCALATION_BLOCKLIST_AAA = frozenset(
    {"fix_input", "verify_subscriber", "enrollment_or_portal_credentials"}
)

VOICE_ESCALATION_ROUTING_STATUSES = frozenset({"INCOMPLETE", "COVERAGE_AMBIGUOUS"})


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


def missing_fields_target(canonical: dict[str, Any]) -> list[str]:
    """Fields the voice agent should ask the payer rep about.

    Specialist-depth topics (frequencies, history, age, pre-auth, downgrades) are appended
    when we are already escalating for a financial/coverage gap — not used alone to trigger
    a call on an otherwise CLEARED check.
    """
    missing = [str(f) for f in (canonical.get("missing_fields") or []) if f]
    if missing:
        for field in specialist_depth_targets(canonical):
            if field not in missing:
                missing.append(field)
        return missing
    if canonical.get("is_covered") is None:
        return ["is_covered", *specialist_depth_targets(canonical)]
    return []


# Human-readable labels for Bland pathway {{requested_benefits}} (missing-only scope).
MISSING_FIELD_VOICE_LABELS: dict[str, str] = {
    "is_active": "whether the member's dental coverage is currently active",
    "is_covered": "whether the requested dental services are covered",
    "in_network": "in-network vs out-of-network status for this provider",
    "deductible_remaining": "remaining individual deductible (exact dollar amount)",
    "annual_max_remaining": "remaining annual maximum benefit (exact dollar amount)",
    "coverage_percent": "coverage percentage for the procedures of interest",
    "copay": "copay amount if applicable",
    "coinsurance": "coinsurance percentage if applicable",
    "prior_auth_required": "whether prior authorization or predetermination is required",
    "frequency_limitations": "frequency limitations (how often cleanings, exams, x-rays, etc. are covered)",
    "waiting_periods": "waiting periods by category or procedure",
    "last_service_dates": "date of last cleaning, exam, bitewings, or other recent services on file",
    "age_limits": "age limits for sealants, fluoride, ortho, or dependents",
    "downgrades": "downgrades or alternate benefits (e.g. composite paid as amalgam)",
}


def specialist_depth_targets(canonical: dict[str, Any]) -> list[str]:
    """Extra VOB topics to ask when electronic check is thin on specialist fields."""
    targets: list[str] = []
    breakdown = canonical.get("dental_benefit_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = {}
    if canonical.get("prior_auth_required") is None:
        targets.append("prior_auth_required")
    if not (breakdown.get("frequency_limitations") or []):
        targets.append("frequency_limitations")
    if not (breakdown.get("waiting_periods") or []):
        targets.append("waiting_periods")
    if not (canonical.get("last_service_dates") or []):
        targets.append("last_service_dates")
    if not (breakdown.get("age_limits") or []):
        targets.append("age_limits")
    if not (breakdown.get("downgrades") or []):
        targets.append("downgrades")
    return targets


def format_missing_fields_for_voice(
    targets: list[str],
    *,
    cdt_codes: list[str] | None = None,
) -> str:
    """Build pathway prompt scope: only ask about gaps from the electronic (Stedi) check."""
    labels: list[str] = []
    for field in targets:
        key = str(field).strip()
        if not key:
            continue
        labels.append(MISSING_FIELD_VOICE_LABELS.get(key, key.replace("_", " ")))
    if not labels:
        return ""
    scope = (
        "ONLY verify these missing items from our electronic eligibility check "
        "(do not re-ask benefits Stedi already confirmed): " + "; ".join(labels)
    )
    codes = [str(c).strip().upper() for c in (cdt_codes or []) if c and str(c).strip()]
    if codes:
        scope += f". Also confirm coverage for CDT procedure codes: {', '.join(codes)}"
    return scope


def canonical_voice_escalation_eligible(canonical: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Canonical-level voice eligibility (no DB). Payer phone + settings checked at queue time.
    """
    if canonical.get("is_active") is False:
        return False, []
    aaa_action = _primary_stedi_action(canonical)
    if aaa_action in VOICE_ESCALATION_BLOCKLIST_AAA:
        return False, []
    targets = missing_fields_target(canonical)
    if not targets:
        return False, []
    return True, targets


def routing_status_voice_eligible(routing_status: str | None) -> bool:
    return (routing_status or "").upper() in VOICE_ESCALATION_ROUTING_STATUSES
