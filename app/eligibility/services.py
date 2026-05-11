"""Orchestration helpers (keeps main.py thin; wires layers 0–6)."""

from __future__ import annotations

import contextlib
import copy
import logging
from typing import Any
from uuid import UUID

from app.eligibility.api_client import build_payload, call_stedi
from app.eligibility.audit import write_audit_event
from app.eligibility.canonical_record import attach_eligibility_canonical_record
from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.cost_calculator import (
    apply_coinsurance_ambiguous_missing_field,
    build_coverage_ambiguous_partial_estimates,
    calculate_responsibility,
)
from app.eligibility.db import (
    fetch_active_provider_payer_network,
    fetch_payer_fee_schedule_as_dict,
    get_supabase,
    insert_eligibility_check,
    insert_procedure_estimates,
)
from app.eligibility.fee_schedule import merge_ucr_fallback_into_fee_schedule
from app.eligibility.integrity import validate_completeness
from app.eligibility.models import EligibilityRequest, TriggerEvent
from app.eligibility.normalizer import normalize
from app.eligibility.router import _routing_state, route
from app.eligibility.triggers import layer0_supabase_validation, resolve_cached_vs_api
from app.eligibility.universal_dental import build_universal_dental_record

logger = logging.getLogger(__name__)


def _attach_fee_network_from_provider_directory(
    canonical: dict[str, Any],
    request: EligibilityRequest,
    supabase: Any,
    trading_partner_service_id: str,
) -> None:
    """
    Set ``in_network_for_fees`` from ``provider_payer_network`` when ``practice_id`` +
    ``rendering_provider_npi`` are supplied. Preserves payer 271 value in ``in_network`` for audit.
    """
    canonical["in_network_from_payer_271"] = canonical.get("in_network")
    pid = (request.practice_id or "").strip()
    npi = (request.rendering_provider_npi or "").strip()
    if not pid or not npi:
        canonical["fee_network_source"] = "payer_271_only"
        return

    row = fetch_active_provider_payer_network(
        supabase,
        practice_id=pid,
        rendering_provider_npi=npi,
        payer_trading_partner_id=trading_partner_service_id,
        provider_service_location_key=request.provider_service_location_key,
    )

    if row is None:
        canonical["fee_network_source"] = "provider_directory_miss"
        return

    canonical["in_network_for_fees"] = bool(row.get("in_network_for_fees"))
    canonical["fee_network_source"] = "provider_directory"
    cl = row.get("contract_label")
    if isinstance(cl, str) and cl.strip():
        canonical["fee_network_contract_label"] = cl.strip()


def _apply_coverage_ambiguous_canonical_enrichment(canonical: dict[str, Any]) -> None:
    canonical["is_covered"] = True
    canonical["coverage_confidence"] = "high"
    for p in canonical.get("procedure_details") or []:
        p["procedure_covered"] = True
    cp = canonical.get("copay")
    if cp is not None:
        with contextlib.suppress(TypeError, ValueError):
            canonical["patient_responsibility"] = float(cp)


def canonical_to_row(
    patient_id: UUID,
    canonical: dict[str, Any],
    *,
    routing_status: str | None,
    has_secondary_flag: bool,
    secondary_payer_id: str | None,
    raw_for_db: dict[str, Any],
) -> dict[str, Any]:
    return {
        "patient_id": str(patient_id),
        "payer_id": canonical.get("payer_id") or "",
        "checked_at": canonical["checked_at"].isoformat(),
        "coverage_order": canonical.get("coverage_order"),
        "is_active": canonical.get("is_active"),
        "inactive_reason": canonical.get("inactive_reason"),
        "is_covered": canonical.get("is_covered"),
        "in_network": canonical.get("in_network"),
        "coverage_percent": canonical.get("coverage_percent"),
        "copay": canonical.get("copay"),
        "coinsurance": canonical.get("coinsurance"),
        "deductible_total": canonical.get("deductible_total"),
        "deductible_met": canonical.get("deductible_met"),
        "deductible_remaining": canonical.get("deductible_remaining"),
        "annual_max_total": canonical.get("annual_max_total"),
        "annual_max_used": canonical.get("annual_max_used"),
        "annual_max_remaining": canonical.get("annual_max_remaining"),
        "has_secondary": has_secondary_flag,
        "secondary_payer_id": secondary_payer_id,
        "raw_response": raw_for_db,
        "response_complete": canonical.get("response_complete"),
        "missing_fields": canonical.get("missing_fields") or [],
        "normalization_version": canonical.get("normalization_version") or "1.0",
        "routing_status": routing_status,
        "integrity_warnings": canonical.get("integrity_warnings") or [],
    }


def run_realtime_pipeline(
    request: EligibilityRequest,
    *,
    settings: EligibilitySettings | None = None,
    coverage_order: str = "primary",
    trading_partner_service_id: str,
) -> dict[str, Any]:
    """
    Layers 2–6 for one payer (primary or secondary flow).
    Returns dict with canonical, routing, check_id, procedure_estimate_rows (if any).
    """
    s = settings or get_settings()
    supabase = get_supabase(s)

    raw = call_stedi(
        build_payload(request, s, trading_partner_service_id=trading_partner_service_id),
        s,
    )
    raw = copy.deepcopy(raw)
    raw["_request_procedure_codes"] = list(request.cdt_codes or [])
    raw["_has_secondary"] = bool(request.secondary_payer_id)
    raw["_secondary_payer_id"] = request.secondary_payer_id
    raw["_trading_partner_service_id"] = trading_partner_service_id

    canonical = normalize(raw, coverage_order)
    # Before Layer 4 so validate_completeness can suppress payer-INN warnings when fee path is directory INN.
    _attach_fee_network_from_provider_directory(
        canonical,
        request,
        supabase,
        trading_partner_service_id,
    )
    validate_completeness(canonical)
    # Decide ambiguous path on pre-enrichment snapshot, enrich, then route so Layer 6 matches final canonical.
    pre_route_state = _routing_state(canonical)
    if pre_route_state == "COVERAGE_AMBIGUOUS":
        _apply_coverage_ambiguous_canonical_enrichment(canonical)
        apply_coinsurance_ambiguous_missing_field(canonical)
        attach_eligibility_canonical_record(canonical)

    routing = route(canonical, supabase)

    payer_for_row = trading_partner_service_id
    canonical["payer_id"] = payer_for_row or canonical.get("payer_id")

    raw_for_db = copy.deepcopy(raw)
    for k in list(raw_for_db.keys()):
        if str(k).startswith("_"):
            del raw_for_db[k]

    row = canonical_to_row(
        request.patient_id,
        canonical,
        routing_status=routing["status"],
        has_secondary_flag=bool(request.secondary_payer_id),
        secondary_payer_id=request.secondary_payer_id,
        raw_for_db=raw_for_db,
    )
    check_id = insert_eligibility_check(supabase, row)

    proc_rows: list[dict[str, Any]] = []
    if canonical.get("is_active") and routing.get("status") == "CLEARED" and canonical.get("response_complete"):
        try:
            fee = fetch_payer_fee_schedule_as_dict(supabase, payer_for_row)
            cdt_for_est = [str(p.get("cdt_code") or "") for p in canonical.get("procedure_details") or []]
            merge_ucr_fallback_into_fee_schedule(fee, payer_for_row, cdt_for_est, s)
            est = calculate_responsibility(canonical, fee)
            detail_by_cdt = {p["cdt_code"]: p for p in canonical.get("procedure_details") or []}
            for e in est:
                d = detail_by_cdt.get(e["cdt_code"], {})
                proc_rows.append(
                    {
                        "cdt_code": e["cdt_code"],
                        "procedure_covered": d.get("procedure_covered"),
                        "waiting_period_end": d.get("waiting_period_end"),
                        "waiting_period_category": d.get("waiting_period_category"),
                        "non_covered_reason": d.get("non_covered_reason"),
                        "allowed_amount": e["allowed_amount"],
                        "insurance_pays": e["insurance_pays"],
                        "patient_responsibility": e["patient_responsibility"],
                    }
                )
            insert_procedure_estimates(supabase, check_id, proc_rows)
        except Exception as ex:
            logger.warning("cost calculation skipped: %s", ex)
    elif pre_route_state == "COVERAGE_AMBIGUOUS":
        proc_rows = build_coverage_ambiguous_partial_estimates(canonical)
        if proc_rows:
            try:
                insert_procedure_estimates(supabase, check_id, proc_rows)
            except Exception as ex:
                logger.warning("partial procedure estimate insert skipped: %s", ex)

    udr = build_universal_dental_record(
        canonical,
        raw_for_db,
        payer_for_row or "",
    )

    primary_out: dict[str, Any] = {
        "check_id": str(check_id),
        "canonical": canonical,
        "routing": routing,
        "procedure_estimates": proc_rows,
        "universal_dental_record": udr.model_dump(mode="json"),
    }
    aaa = canonical.get("payer_aaa_errors")
    if isinstance(aaa, list) and aaa:
        primary_out["payer_aaa_errors"] = aaa
    return primary_out


def run_eligibility_check_endpoint(
    request: EligibilityRequest,
    *,
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    """Full POST /eligibility/check flow including Layer 0–1."""
    s = settings or get_settings()

    if request.trigger_event is TriggerEvent.BATCH_SWEEP:
        raise ValueError("Use POST /eligibility/batch for BATCH_SWEEP")

    if request.ssn:
        write_audit_event(
            patient_id=request.patient_id,
            event_type="SSN_FALLBACK",
            detail={"reason": "SSN present on request; subscriber_id path preferred; proceeding with audited fallback path"},
            settings=s,
        )

    request, layer0_warnings = layer0_supabase_validation(request, settings=s)

    mode, cached = resolve_cached_vs_api(request, settings=s)
    if mode == "cache" and cached is not None:
        write_audit_event(
            patient_id=request.patient_id,
            event_type="CACHE_HIT",
            detail={"payer_id": request.primary_payer_id, "check_id": cached.get("id")},
            settings=s,
        )
        return {
            "cached": True,
            "record": cached,
            "layer0_warnings": layer0_warnings,
        }

    primary_result = run_realtime_pipeline(
        request,
        settings=s,
        coverage_order="primary",
        trading_partner_service_id=request.primary_payer_id,
    )
    results = [primary_result]

    if request.secondary_payer_id:
        secondary_result = run_realtime_pipeline(
            request,
            settings=s,
            coverage_order="secondary",
            trading_partner_service_id=request.secondary_payer_id,
        )
        results.append(secondary_result)

    write_audit_event(
        patient_id=request.patient_id,
        event_type="ROUTING",
        detail={"results": [{"routing": r["routing"], "check_id": r["check_id"]} for r in results]},
        settings=s,
    )

    return {
        "cached": False,
        "layer0_warnings": layer0_warnings,
        "primary": results[0],
        "secondary": results[1] if len(results) > 1 else None,
    }


