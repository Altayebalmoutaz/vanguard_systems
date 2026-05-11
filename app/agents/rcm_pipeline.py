"""
RCM pipeline: Coding → Prior Auth → Claim (synchronous, linear).
"""

from __future__ import annotations

from app.agents.claim_agent import run_claim_draft_agent
from app.agents.coding_agent import run_coding_agent
from app.agents.prior_auth_agent import run_prior_auth_agent
from app.config import Settings
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

    patient, provider, billing = _resolve_claim_context(request, supabase)

    claim_request = ClaimAgentRequest(
        coding=coding,
        prior_auth=prior_auth,
        patient=patient,
        provider=provider,
        billing=billing,
    )
    claim_draft = run_claim_draft_agent(claim_request)

    return FullRcmPipelineResponse(
        coding=coding,
        prior_auth=prior_auth,
        claim_draft=claim_draft,
    )


def _resolve_claim_context(
    request: FullRcmPipelineRequest,
    supabase: Client | None,
) -> tuple[PatientInfo, ProviderInfo, ClaimBillingInput]:
    """
    Resolve patient/provider/billing for claim stage.
    Priority:
      1) Direct values supplied in request.
      2) Snapshot lookup by encounter_id from Supabase.
    """
    if request.patient and request.provider and request.billing:
        return request.patient, request.provider, request.billing

    if not request.encounter_id:
        raise RuntimeError(
            "Claim context missing: provide patient/provider/billing or encounter_id."
        )
    if supabase is None:
        raise RuntimeError("Supabase client is required for encounter_id snapshot lookup.")

    snapshot = _fetch_claim_snapshot(supabase, request.encounter_id)
    if not snapshot:
        raise RuntimeError(f"No claim intake snapshot found for encounter_id={request.encounter_id}")
    if not snapshot.get("ready_for_claim", False):
        raise RuntimeError(
            f"Snapshot encounter_id={request.encounter_id} is not ready_for_claim."
        )

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


def _fetch_claim_snapshot(supabase: Client, encounter_id: str) -> dict | None:
    """Load claim intake snapshot using RPC first, then direct table lookup fallback."""
    try:
        rpc_resp = (
            supabase.rpc("get_claim_intake_snapshot", {"p_encounter_id": encounter_id}).execute()
        )
        if isinstance(rpc_resp.data, dict):
            return rpc_resp.data
        if isinstance(rpc_resp.data, list) and rpc_resp.data:
            first = rpc_resp.data[0]
            if isinstance(first, dict):
                return first
    except Exception:
        # Fallback below handles environments where RPC is unavailable.
        pass

    try:
        table_resp = (
            supabase.table("claim_intake_snapshot")
            .select("*")
            .eq("encounter_id", encounter_id)
            .limit(1)
            .execute()
        )
        if isinstance(table_resp.data, list) and table_resp.data:
            first = table_resp.data[0]
            if isinstance(first, dict):
                return first
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load claim intake snapshot for encounter_id={encounter_id}: {exc}"
        ) from exc

    return None
