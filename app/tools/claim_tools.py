"""
Claim submission tools: build structured claim + Stedi clearinghouse submit.

No LLM — pure logic. ``submit_claim_tool`` delegates to the real Stedi 837 adapter
when ``Settings.stedi_claims_api_key`` is configured. Mock submission
(``stedi_mock``) is opt-in via ``Settings.allow_claim_mock_submission`` for local
development and offline tests only — production must not fall back on adapter errors.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.config import Settings, get_settings
from app.integrations.stedi_claims import submit_dental_claim
from app.schemas.claim import (
    ClaimAgentRequest,
    ClaimCodesBlock,
    ClaimPatientBlock,
    ClaimProviderBlock,
    ClaimStructure,
)

logger = logging.getLogger(__name__)


class ClaimSubmissionError(RuntimeError):
    """Raised when a claim cannot be submitted to a configured clearinghouse."""


def build_claim_tool(data: ClaimAgentRequest) -> dict[str, Any]:
    """
    Assemble a simplified claim object from coding output + patient + provider.

    Returns a JSON-serializable dict matching the agreed claim shape.
    """
    structure = ClaimStructure(
        patient=ClaimPatientBlock(
            name=data.patient.name.strip(),
            dob=data.patient.dob.strip(),
        ),
        provider=ClaimProviderBlock(
            name=data.provider.name.strip(),
            npi=data.provider.npi.strip(),
        ),
        subscriber={
            "member_id": data.billing.subscriber.member_id.strip(),
            "relationship_to_patient": data.billing.subscriber.relationship_to_patient,
            "name": data.billing.subscriber.name.strip(),
            "dob": data.billing.subscriber.dob.strip(),
            "address": data.billing.subscriber.address.model_dump(),
        },
        payer=data.billing.payer.model_dump(),
        billing_provider=data.billing.billing_provider.model_dump(),
        rendering_provider=data.billing.rendering_provider.model_dump(),
        patient_address=data.billing.patient_address.model_dump(),
        patient_sex=data.billing.patient_sex,
        claim_frequency_code=data.billing.claim_frequency_code,
        place_of_service=data.billing.place_of_service,
        patient_account_number=data.billing.patient_account_number,
        diagnosis_codes=list(data.billing.diagnosis_codes),
        service_lines=[
            {
                "line_number": ln.line_number,
                "service_date": ln.service_date.strip(),
                "cdt_code": ln.cdt_code.strip(),
                "units": ln.units,
                "charge_amount": ln.charge_amount,
                "diagnosis_pointers": list(ln.diagnosis_pointers),
                "tooth_number": ln.tooth_number,
                "surface": ln.surface,
                "prior_auth_number": ln.prior_auth_number,
            }
            for ln in data.billing.service_lines
        ],
        total_charge_amount=data.billing.total_charge_amount,
        codes=ClaimCodesBlock(
            cdt=list(data.coding.cdt_codes),
            icd10=list(data.coding.icd10_codes),
        ),
    )
    # Pydantic v2 model_dump for plain dict
    return structure.model_dump()


def submit_claim_tool(
    claim: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Submit a claim to the configured clearinghouse.

    Behaviour:

    * If ``Settings.stedi_claims_api_key`` is set → POST to Stedi's Healthcare
      Claims API via :func:`app.integrations.stedi_claims.submit_dental_claim`.
      Adapter failures propagate (no mock fallback).
    * If no API key and ``Settings.allow_claim_mock_submission`` is true → return
      the deterministic ``stedi_mock`` shape (tests / local dev only).
    * Otherwise → raise :class:`ClaimSubmissionError`.

    The return shape is intentionally narrow (``claim_id``, ``status``,
    ``submission_channel``) so downstream code can stay vendor-agnostic.
    """
    s = settings or get_settings()
    if s.stedi_claims_api_key:
        return submit_dental_claim(claim, s)

    if not s.allow_claim_mock_submission:
        raise ClaimSubmissionError(
            "STEDI_CLAIMS_API_KEY is not configured and mock claim submission is disabled"
        )

    suffix = secrets.randbelow(90_000) + 10_000
    return {
        "claim_id": f"CLM{suffix}",
        "status": "submitted",
        "submission_channel": "stedi_mock",
    }
