"""
RCM pipeline: Coding → Prior Auth → Claim (synchronous, linear).
"""

from __future__ import annotations

from app.agents.claim_agent import run_claim_draft_agent
from app.agents.coding_agent import run_coding_agent
from app.agents.prior_auth_agent import run_prior_auth_agent
from app.config import Settings
from app.integrations.claim_snapshots import fetch_claim_intake_snapshot
from app.rcm.claims_store import persist_claim_draft
from app.schemas.claim import (
    ClaimAgentRequest,
    ClaimBillingInput,
    FullRcmPipelineRequest,
    FullRcmPipelineResponse,
    PatientInfo,
    ProviderInfo,
)
from app.schemas.coding import CodingAgentRequest
from app.schemas.prior_auth import PriorAuthAgentRequest, RcmPipelineResponse
from supabase import Client


def run_rcm_pipeline(
    settings: Settings,
    supabase: Client | None,
    request: CodingAgentRequest,
) -> RcmPipelineResponse:
    """
    Clinical input → coding (LLM + validations) → prior auth (rules + LLM).
    """
    coding = run_coding_agent(settings, supabase, request)

    prior_request = PriorAuthAgentRequest(
        coding=coding,
        insurance=request.insurance,
        clinical_note=request.clinical_note,
        patient_age=request.patient_age,
        patient_id=request.patient_id,
        practice_id=request.practice_id,
    )
    prior_auth = run_prior_auth_agent(settings, prior_request)

    return RcmPipelineResponse(coding=coding, prior_auth=prior_auth)


def run_full_rcm_pipeline(
    settings: Settings,
    supabase: Client | None,
    request: FullRcmPipelineRequest,
) -> FullRcmPipelineResponse:
    """
    End-to-end draft flow: coding_agent → prior_auth_agent → claim draft.
    The draft is intended for biller review/edit before explicit submit.
    """
    coding_req = CodingAgentRequest(
        clinical_note=request.clinical_note,
        patient_age=request.patient_age,
        insurance=request.insurance,
        patient_id=request.patient_id,
        practice_id=request.practice_id,
    )
    coding = run_coding_agent(settings, supabase, coding_req)

    prior_request = PriorAuthAgentRequest(
        coding=coding,
        insurance=request.insurance,
        clinical_note=request.clinical_note,
        patient_age=request.patient_age,
        patient_id=request.patient_id,
        practice_id=request.practice_id,
    )
    prior_auth = run_prior_auth_agent(settings, prior_request)

    patient, provider, billing = _resolve_claim_context(
        settings,
        request,
        practice_id=request.practice_id,
    )

    claim_request = ClaimAgentRequest(
        coding=coding,
        prior_auth=prior_auth,
        patient=patient,
        provider=provider,
        billing=billing,
    )
    claim_draft = run_claim_draft_agent(claim_request)

    claim_record_id: str | None = None
    if request.practice_id:
        claim_record_id = persist_claim_draft(
            settings,
            practice_id=request.practice_id,
            patient_id=request.patient_id,
            clinical_note=request.clinical_note,
            provider=provider.name if provider else None,
            coding=coding.model_dump(),
            prior_auth=prior_auth.model_dump(),
            claim_draft=claim_draft.model_dump(),
        )

    response = FullRcmPipelineResponse(
        coding=coding,
        prior_auth=prior_auth,
        claim_draft=claim_draft,
        claim_record_id=claim_record_id,
    )
    return response


def _resolve_claim_context(
    settings: Settings,
    request: FullRcmPipelineRequest,
    *,
    practice_id: str | None = None,
) -> tuple[PatientInfo, ProviderInfo, ClaimBillingInput]:
    """
    Resolve patient/provider/billing for claim stage.
    Priority:
      1) Direct values supplied in request.
      2) Snapshot lookup by encounter_id from Neon or Supabase.
    """
    if request.patient and request.provider and request.billing:
        return request.patient, request.provider, request.billing

    if not request.encounter_id:
        raise RuntimeError(
            "Claim context missing: provide patient/provider/billing or encounter_id."
        )

    snapshot = fetch_claim_intake_snapshot(
        settings,
        request.encounter_id,
        practice_id=practice_id,
    )
    if not snapshot:
        raise RuntimeError(
            f"No claim intake snapshot found for encounter_id={request.encounter_id}"
        )
    if not snapshot.get("ready_for_claim", False):
        raise RuntimeError(f"Snapshot encounter_id={request.encounter_id} is not ready_for_claim.")

    patient_payload = snapshot.get("patient") or {}
    rendering_provider = snapshot.get("rendering_provider") or {}
    billing_provider = snapshot.get("billing_provider") or {}
    claim_header = snapshot.get("claim_header") or {}
    financials = snapshot.get("financials") or {}

    provider_payload = {
        "name": rendering_provider.get("name") or billing_provider.get("name"),
        "npi": rendering_provider.get("npi") or billing_provider.get("npi"),
    }
    billing_payload = {
        "claim_frequency_code": claim_header.get("claim_frequency_code", "1"),
        "place_of_service": claim_header.get("place_of_service"),
        "patient_account_number": claim_header.get("patient_account_number"),
        "patient_sex": claim_header.get("patient_sex"),
        "patient_address": patient_payload.get("address"),
        "subscriber": snapshot.get("subscriber"),
        "billing_provider": billing_provider,
        "rendering_provider": rendering_provider,
        "payer": snapshot.get("payer"),
        "diagnosis_codes": snapshot.get("diagnosis_codes"),
        "service_lines": snapshot.get("service_lines"),
        "total_charge_amount": financials.get("total_charge_amount"),
    }

    try:
        patient = PatientInfo.model_validate(
            {"name": patient_payload["name"], "dob": patient_payload["dob"]}
        )
        provider = ProviderInfo.model_validate(provider_payload)
        billing = ClaimBillingInput.model_validate(billing_payload)
    except Exception as exc:  # pydantic validation error or missing keys
        raise RuntimeError(
            f"Invalid claim snapshot for encounter_id={request.encounter_id}: {exc}"
        ) from exc

    return patient, provider, billing
