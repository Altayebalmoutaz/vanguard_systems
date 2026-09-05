"""Versioned CARC posting policy for Remit Control Phase 3.

Suggests the next RCM action from an 835 CAS code. Never writes the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

POLICY_VERSION = "v1"

CasBucket = Literal[
    "write_off",
    "patient_resp",
    "denial_actionable",
    "cob_secondary",
    "administrative",
    "underpay",
    "none",
]

# Remark codes that usually mean "attach radiograph and resubmit" (not appeal).
XRAY_REMARKS = frozenset({"M127", "N221", "N222", "N223", "N706"})


@dataclass(frozen=True)
class CarcPolicy:
    """One versioned CARC rule. ``writes_ledger`` is always False in v1."""

    carc: str
    bucket: CasBucket
    next_action: str
    bill_patient: bool
    appeal: bool
    writes_ledger: bool = False
    note: str = ""


# Book 4 — paraphrased X12 CARC map. Confirm current WPC text before expanding.
_CARC_POLICY_V1: dict[str, CarcPolicy] = {
    "1": CarcPolicy("1", "patient_resp", "bill_patient", True, False, note="deductible"),
    "2": CarcPolicy("2", "patient_resp", "bill_patient", True, False, note="coinsurance"),
    "3": CarcPolicy("3", "patient_resp", "bill_patient", True, False, note="copay"),
    "16": CarcPolicy(
        "16",
        "denial_actionable",
        "attach_and_resubmit",
        False,
        False,
        note="lacks information — read RARC, correct, resubmit",
    ),
    "18": CarcPolicy(
        "18",
        "administrative",
        "verify_duplicate",
        False,
        False,
        note="duplicate — do not resubmit",
    ),
    "22": CarcPolicy(
        "22",
        "cob_secondary",
        "bill_secondary",
        False,
        False,
        note="coordination of benefits",
    ),
    "29": CarcPolicy(
        "29",
        "administrative",
        "write_off",
        False,
        False,
        note="timely filing — appeal only with proof",
    ),
    "45": CarcPolicy(
        "45",
        "write_off",
        "write_off",
        False,
        False,
        note="contractual / fee schedule — never bill patient",
    ),
    "50": CarcPolicy(
        "50",
        "denial_actionable",
        "review_eob_and_prepare_appeal",
        False,
        True,
        note="medical necessity",
    ),
    "96": CarcPolicy(
        "96",
        "patient_resp",
        "notify_patient",
        True,
        False,
        note="non-covered — bill patient only if disclosed",
    ),
    "97": CarcPolicy(
        "97",
        "write_off",
        "write_off",
        False,
        False,
        note="bundled — never bill patient",
    ),
    "109": CarcPolicy(
        "109",
        "cob_secondary",
        "rebill_correct_payer",
        False,
        False,
        note="wrong payer / contractor",
    ),
    "119": CarcPolicy(
        "119",
        "patient_resp",
        "bill_patient",
        True,
        False,
        note="benefit maximum reached",
    ),
    "151": CarcPolicy(
        "151",
        "denial_actionable",
        "review_contract_and_patient_balance",
        False,
        False,
        note="frequency",
    ),
    "197": CarcPolicy(
        "197",
        "denial_actionable",
        "obtain_prior_auth",
        False,
        False,
        note="precert / authorization absent",
    ),
}

WRITE_OFF_CARCS = frozenset(
    code for code, policy in _CARC_POLICY_V1.items() if policy.bucket == "write_off"
)
ROUTINE_PR_CARCS = frozenset({"1", "2", "3"})
HITL_BUCKETS = frozenset({"denial_actionable", "patient_resp", "underpay", "cob_secondary"})
BUCKET_PRIORITY: dict[CasBucket, int] = {
    "cob_secondary": 0,
    "denial_actionable": 1,
    "administrative": 2,
    "underpay": 3,
    "patient_resp": 4,
    "write_off": 5,
    "none": 6,
}


def parse_reason_token(reason_token: str) -> tuple[str, str]:
    """Split ``CO-96`` / ``96`` into ``(group, carc)``. Mock tokens return empty strings."""
    token = (reason_token or "").strip().upper()
    if not token:
        return "", ""
    if "-" in token:
        group, _, rest = token.partition("-")
        if group in {"CO", "PR", "OA", "PI", "CR"} and rest:
            return group, rest.lstrip("0") or rest
        return "", token
    if token.isdigit():
        return "", token.lstrip("0") or token
    return "", ""


def lookup_carc(reason_code: str, *, remark_codes: list[str] | None = None) -> CarcPolicy | None:
    """Return the v1 policy for a CARC, with RARC refinements for 16 / 96."""
    carc = (reason_code or "").strip().lstrip("0") or (reason_code or "").strip()
    if not carc:
        return None
    policy = _CARC_POLICY_V1.get(carc)
    if policy is None:
        return None
    remarks = {str(code).strip().upper() for code in (remark_codes or []) if code}
    if carc == "16" and remarks & XRAY_REMARKS:
        return CarcPolicy(
            carc="16",
            bucket="denial_actionable",
            next_action="upload_xray_and_resubmit",
            bill_patient=False,
            appeal=False,
            note="missing radiograph (RARC)",
        )
    return policy


def lookup_reason_token(
    reason_token: str, *, remark_codes: list[str] | None = None
) -> CarcPolicy | None:
    _, carc = parse_reason_token(reason_token)
    if not carc:
        return None
    return lookup_carc(carc, remark_codes=remark_codes)


def should_queue_patient_resp(*, claim_status_code: str, carc: str) -> bool:
    """Routine PR 1/2/3 on a paid claim is EOB math, not a worklist item."""
    status = str(claim_status_code or "").strip()
    if carc in {"96", "119"}:
        return True
    if status in {"4", "denied"}:
        return True
    if carc in ROUTINE_PR_CARCS and status in {"1", "2", "3"}:
        return False
    return carc not in ROUTINE_PR_CARCS


def should_generate_appeal(era_status: str, reason_token: str) -> bool:
    """CARC policy owns appeal vs correction. Mock tokens keep prior denied=appeal behavior."""
    if era_status != "denied":
        return False
    policy = lookup_reason_token(reason_token)
    if policy is not None:
        return policy.appeal
    return True
