"""
Denial / ERA tools: parse mock 835, map reasons, appeal letters, resubmission checklists.

No LLM — modular and easy to swap for real X12 835 parsing.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.llm.denial_llm import llm_denial_intelligence


def parse_era_tool(response: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a mock ERA dict to {status, reason}.

    If you pass a full request-shaped dict with nested `mock_era`, extracts that first.
    """
    if "mock_era" in response and isinstance(response["mock_era"], dict):
        response = response["mock_era"]
    status = str(response.get("status") or "paid").lower().strip()
    if status not in ("paid", "denied", "partial"):
        status = "paid"
    reason = str(response.get("reason") or "").strip()
    return {"status": status, "reason": reason}


def detect_denial_reason_tool(parsed_era: dict[str, Any], claim_snapshot: dict[str, Any]) -> str:
    """
    Machine-friendly reason token (echoes ERA reason or defaults by status).
    """
    _ = claim_snapshot
    status = parsed_era.get("status")
    reason = (parsed_era.get("reason") or "").strip()

    if status == "paid":
        return ""

    if reason:
        return reason

    if status == "denied":
        return "unspecified_denial"

    if status == "partial":
        return "unspecified_partial_adjustment"

    return ""


def map_denial_reason_tool(era_status: str, reason_token: str) -> str:
    """
    Map ERA outcome + reason token to the next operational action.

    Spec (denied):
      missing_xray → upload_xray_and_resubmit
      invalid_code → correct_code_and_resubmit
      not_covered → notify_patient
    Paid → none.
    """
    if era_status == "paid":
        return "none"

    if era_status == "partial":
        if reason_token == "frequency_limit":
            return "review_contract_and_patient_balance"
        return "review_eob_and_remaining_balance"

    # denied
    spec_map = {
        "missing_xray": "upload_xray_and_resubmit",
        "invalid_code": "correct_code_and_resubmit",
        "not_covered": "notify_patient",
    }
    if reason_token in spec_map:
        return spec_map[reason_token]

    if reason_token == "unspecified_denial":
        return "review_eob_and_prepare_appeal"

    return "review_eob_and_prepare_appeal"


# Backward-compatible name for older tests / callers
def suggest_action_tool(era_status: str, reason_token: str) -> str:
    return map_denial_reason_tool(era_status, reason_token)


def _friendly_reason_for_letter(reason_token: str) -> str:
    """Plain-language phrase for appeal body."""
    pretty = {
        "missing_xray": "missing or insufficient radiographic documentation",
        "invalid_code": "coding edits reported on the explanation of benefits",
        "not_covered": "services being classified as not covered under the current benefit determination",
        "frequency_limit": "frequency or benefit limitations",
        "unspecified_denial": "the payer’s stated denial reason on the EOB",
        "claim_not_submitted": "the claim not yet being on file with the payer",
    }
    return pretty.get(reason_token, reason_token.replace("_", " ") or "the stated denial reason")


def generate_appeal_letter_tool(data: dict[str, Any]) -> str:
    """
    Build a full appeal letter when a claim is denied (empty string if not applicable).

    Expected keys: insurance_company_name, claim_id, patient_name, reason_token,
    cdt_codes, icd10_codes, provider_name
    """
    insurance = str(data.get("insurance_company_name") or "the patient's dental benefit plan").strip()
    claim_id = str(data.get("claim_id") or "").strip()
    patient = str(data.get("patient_name") or "the patient").strip()
    reason_token = str(data.get("reason_token") or "").strip()
    reason_line = _friendly_reason_for_letter(reason_token)
    cdt = data.get("cdt_codes") or []
    icd = data.get("icd10_codes") or []
    cdt_str = ", ".join(str(x) for x in cdt) if cdt else "(see attached treatment record)"
    icd_str = ", ".join(str(x) for x in icd) if icd else "(see attached diagnosis documentation)"
    provider = str(data.get("provider_name") or "Dental Practice").strip()

    return f"""To: {insurance}
Attn: Claims Review Department

Re: Appeal for Claim ID {claim_id}

Dear Claims Reviewer,

We are submitting this appeal for the above-referenced claim for patient {patient}. The claim was denied due to {reason_line}. Upon review, all required documentation and clinical justification have been provided:

- CDT Codes: {cdt_str}
- ICD-10 Codes: {icd_str}
- Clinical Documentation: Provided

We respectfully request that you reconsider this claim and approve payment in accordance with the patient’s coverage.

Thank you for your attention to this matter.

Sincerely,
{provider}
"""


def auto_resubmit_tool(claim_id: str, next_action: str) -> list[str]:
    """
    Auto-prepared checklist for resubmission / follow-up (operational hints, not an API call).
    """
    cid = claim_id or "(pending claim id)"
    steps_by_action: dict[str, list[str]] = {
        "none": [],
        "upload_xray_and_resubmit": [
            f"Pull or acquire radiographs tied to claim {cid}",
            "Index images in PMS / imaging module with tooth numbers and date of service",
            f"Resubmit claim {cid} through clearinghouse with attachments per payer rules",
            "Track new reference number and follow ERA in 5–14 business days",
        ],
        "correct_code_and_resubmit": [
            f"Open original claim {cid} and EOB denial line items",
            "Update CDT/ICD per payer remark codes; document rationale in clinical note",
            f"Void/correct and resubmit (or submit corrected claim) for {cid} per clearinghouse workflow",
            "Retain audit trail of before/after codes",
        ],
        "notify_patient": [
            f"Document benefit determination for claim {cid} in patient ledger",
            "Generate patient financial estimate / statement for non-covered portion",
            "Notify patient (call + portal message) and obtain consent for self-pay or alternate treatment",
        ],
        "review_eob_and_remaining_balance": [
            f"Post partial payment for {cid} and reconcile allowed vs billed",
            "Identify patient responsibility vs write-off per contract",
            "Schedule follow-up if secondary billing applies",
        ],
        "review_contract_and_patient_balance": [
            "Verify frequency/limit language in employer benefit summary",
            "Adjust patient balance or appeal if medical necessity supports exception",
        ],
        "review_eob_and_prepare_appeal": [
            f"Gather clinical notes, images, and narratives supporting claim {cid}",
            "Complete payer appeal form if required; attach this generated letter",
            "Submit via portal/fax/mail per plan instructions and log tracking number",
        ],
        "resolve_prior_auth_then_submit_claim": [
            "Complete prior authorization or required documentation checklist",
            "Submit original claim only after payer confirms eligibility for services",
        ],
    }
    return list(steps_by_action.get(next_action, [f"Review ERA for claim {cid} and execute payer-specific workflow"]))


def denial_llm_intelligence_tool(
    settings: Settings,
    parsed_era: dict[str, Any],
    claim_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """
    LLM pre-triage interpretation layer.
    Deterministic reason/action mapping remains authoritative in the agent.
    """
    payload = {
        "era": parsed_era,
        "claim_snapshot": claim_snapshot,
    }
    return llm_denial_intelligence(settings, payload)
