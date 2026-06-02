"""FastAPI service — Vanguard MD Eligibility Agent."""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.errors import sanitized_http_exception
from app.eligibility.api_client import build_payload, call_stedi_batch
from app.eligibility.audit import write_audit_event
from app.eligibility.cob import calculate_cob
from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.db import (
    get_eligibility_check_by_id,
    get_latest_eligibility_for_patient,
    get_supabase,
    list_audit_for_patient,
    list_procedure_estimates,
)
from app.eligibility.models import (
    CobRequest,
    EligibilityBatchRequest,
    EligibilityRequest,
    Layer1ValidationError,
    StediAPIError,
    TriggerEvent,
    TwoPassEligibilityCodingRequest,
    TwoPassEligibilityCodingResponse,
)
from app.eligibility.services import run_eligibility_check_endpoint
from app.eligibility.triggers import layer0_supabase_validation
from app.integrations.opendental import (
    OpenDentalAPIError,
    OpenDentalClient,
    OpenDentalConfigError,
    OpenDentalMappingError,
    od_to_eligibility_request,
)
from app.integrations.opendental.poller import start_appointment_poller
from app.integrations.opendental.writeback import run_opendental_writeback
from app.security.phi import scrub_for_log

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _stedi_http_exception(exc: StediAPIError) -> HTTPException:
    """Map Stedi errors to a sanitized 502.

    The Stedi 271 echo can contain patient PHI (member name, DOB, ID) and must
    not be reflected back to API clients. We log the parsed body server-side
    via the standard error helper (which scrubs PHI) and return only the
    upstream HTTP status to the caller.
    """
    body_preview: Any = None
    if exc.body:
        try:
            body_preview = json.loads(exc.body)
        except json.JSONDecodeError:
            body_preview = exc.body
    log_msg = f"Stedi API failure status={exc.status_code} body={scrub_for_log(repr(body_preview))}"
    return sanitized_http_exception(
        502,
        public_message="Eligibility clearinghouse error",
        log_message=log_msg,
        exc=exc,
        extra={"upstream_status": exc.status_code},
    )


def _opendental_http_exception(exc: OpenDentalAPIError) -> HTTPException:
    body_preview: Any = None
    if exc.body:
        try:
            body_preview = json.loads(exc.body)
        except json.JSONDecodeError:
            body_preview = exc.body
    log_msg = (
        "OpenDental API failure "
        f"status={exc.status_code} body={scrub_for_log(repr(body_preview))}"
    )
    return sanitized_http_exception(
        502,
        public_message="Practice management API error",
        log_message=log_msg,
        exc=exc,
        extra={"upstream_status": exc.status_code},
    )


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the OpenDental appointment poller on startup (if enabled); cancel on shutdown."""
    settings = get_settings()
    poller_task = None
    if settings.opendental_auto_poll_enabled:
        poller_task = start_appointment_poller(run_from_opendental, settings)
        logger.info("OpenDental auto-poll enabled (interval=%ss)", settings.opendental_auto_poll_interval_seconds)
    try:
        yield
    finally:
        if poller_task is not None:
            poller_task.cancel()
            with contextlib.suppress(Exception):
                await poller_task


app = FastAPI(title="Vanguard MD Eligibility Agent", version="0.1.0", lifespan=_lifespan)


class EligibilityAgentApiKeyMiddleware(BaseHTTPMiddleware):
    """Mirrors Authorization from Supabase Edge (`process-eligibility-request`)."""

    async def dispatch(self, request: Request, call_next):
        cfg = get_settings()
        key = (cfg.eligibility_agent_api_key or "").strip()
        if not key:
            return await call_next(request)
        path = request.url.path or ""
        if request.method == "GET" and path.rstrip("/").endswith("/health"):
            return await call_next(request)
        auth = request.headers.get("authorization") or ""
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "missing_or_invalid_bearer"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth[7:]
        if not secrets.compare_digest(token, key):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid_api_key"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


app.add_middleware(EligibilityAgentApiKeyMiddleware)


class EligibilityCheckHttpResponse(BaseModel):
    cached: bool = False
    layer0_warnings: list[str] = Field(default_factory=list)
    primary: dict[str, Any] | None = None
    secondary: dict[str, Any] | None = None
    record: dict[str, Any] | None = None


class FromOpenDentalRequest(BaseModel):
    pat_num: int = Field(..., ge=1)
    trigger_event: TriggerEvent = TriggerEvent.PRE_APPOINTMENT
    cdt_codes: list[str] | None = None
    practice_id: str | None = None
    rendering_provider_npi: str | None = None
    write_back: bool = False


class FromOpenDentalResponse(EligibilityCheckHttpResponse):
    opendental: dict[str, Any] | None = None


@app.post("/eligibility/check", response_model=EligibilityCheckHttpResponse)
def post_eligibility_check(body: EligibilityRequest) -> EligibilityCheckHttpResponse:
    """Single real-time eligibility (Layers 0–6); secondary payer = second independent Stedi call."""
    try:
        out = run_eligibility_check_endpoint(body, settings=get_settings())
        return EligibilityCheckHttpResponse(**out)
    except Layer1ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": e.code.value, "message": str(e), "layer": "layer1", "detail": e.detail},
        ) from e
    except ValueError as e:
        raise sanitized_http_exception(
            400,
            public_message="Invalid eligibility request payload",
            log_message="run_eligibility_check_endpoint ValueError",
            exc=e,
        ) from e
    except StediAPIError as e:
        raise _stedi_http_exception(e) from e
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message="Eligibility service is temporarily unavailable",
            log_message="run_eligibility_check_endpoint RuntimeError",
            exc=e,
        ) from e


def run_from_opendental(
    *,
    pat_num: int,
    trigger_event: TriggerEvent = TriggerEvent.PRE_APPOINTMENT,
    cdt_codes: list[str] | None = None,
    practice_id: str | None = None,
    rendering_provider_npi: str | None = None,
    write_back: bool = False,
    settings: EligibilitySettings | None = None,
    client: OpenDentalClient | None = None,
) -> dict[str, Any]:
    """Core OpenDental -> eligibility -> write-back flow shared by the route and poller.

    Raises OpenDental*/Stedi*/Layer1 errors to the caller (the HTTP route maps them to
    responses; the poller logs and continues).
    """
    settings = settings or get_settings()
    client = client or OpenDentalClient.from_settings(settings)

    patient = client.get_patient(pat_num)
    insurance_rows = client.get_patient_insurance(pat_num)

    carriers_by_num: dict[int, Any] = {}
    for row in insurance_rows:
        if row.CarrierNum not in carriers_by_num:
            carriers_by_num[row.CarrierNum] = client.get_carrier(row.CarrierNum)

    mapped = od_to_eligibility_request(
        patient,
        insurance_rows,
        carriers_by_num,
        trigger_event=trigger_event,
        cdt_codes=cdt_codes,
        practice_id=practice_id,
        rendering_provider_npi=rendering_provider_npi,
    )
    out = run_eligibility_check_endpoint(mapped.request, settings=settings)

    writeback_detail: dict[str, Any] | None = None
    primary = out.get("primary") or {}
    if write_back and settings.opendental_writeback_enabled and primary:
        writeback_detail = run_opendental_writeback(
            client,
            pat_num=pat_num,
            primary_pat_plan_num=mapped.primary_pat_plan_num,
            primary_plan_num=mapped.primary_plan_num,
            primary_ins_sub_num=mapped.primary_ins_sub_num,
            primary_result=primary,
            carrier_name=mapped.primary_carrier_name,
            write_benefit_notes=settings.opendental_write_benefit_notes_enabled,
            write_subscriber_note=settings.opendental_write_subscriber_note_enabled,
            write_commlog=settings.opendental_write_commlog_enabled,
            write_insadjust=settings.opendental_write_insadjust_enabled,
            write_benefits_grid=settings.opendental_write_benefits_grid_enabled,
        )

    opendental_detail = {
        "pat_num": pat_num,
        "primary_pat_plan_num": mapped.primary_pat_plan_num,
        "primary_plan_num": mapped.primary_plan_num,
        "primary_ins_sub_num": mapped.primary_ins_sub_num,
        "write_back_requested": write_back,
        "write_back_enabled": settings.opendental_writeback_enabled,
        "write_back_result": (writeback_detail or {}).get("write_back_result"),
        "write_back_notes": writeback_detail,
    }
    return {**out, "opendental": opendental_detail}


@app.post("/eligibility/from-opendental", response_model=FromOpenDentalResponse)
def post_eligibility_from_opendental(body: FromOpenDentalRequest) -> FromOpenDentalResponse:
    settings = get_settings()
    try:
        out = run_from_opendental(
            pat_num=body.pat_num,
            trigger_event=body.trigger_event,
            cdt_codes=body.cdt_codes,
            practice_id=body.practice_id,
            rendering_provider_npi=body.rendering_provider_npi,
            write_back=body.write_back,
            settings=settings,
        )
        return FromOpenDentalResponse(**out)
    except OpenDentalConfigError as e:
        raise sanitized_http_exception(
            503,
            public_message="OpenDental connector is not configured",
            log_message="OpenDentalConfigError in from-opendental route",
            exc=e,
        ) from e
    except OpenDentalMappingError as e:
        raise sanitized_http_exception(
            422,
            public_message="OpenDental patient could not be mapped to eligibility request",
            log_message="OpenDentalMappingError in from-opendental route",
            exc=e,
        ) from e
    except OpenDentalAPIError as e:
        raise _opendental_http_exception(e) from e
    except Layer1ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": e.code.value, "message": str(e), "layer": "layer1", "detail": e.detail},
        ) from e
    except ValueError as e:
        raise sanitized_http_exception(
            400,
            public_message="Invalid eligibility request payload",
            log_message="from-opendental ValueError",
            exc=e,
        ) from e
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message="Eligibility service is temporarily unavailable",
            log_message="from-opendental RuntimeError",
            exc=e,
        ) from e


@app.post("/eligibility/two-pass", response_model=TwoPassEligibilityCodingResponse)
def post_eligibility_two_pass(body: TwoPassEligibilityCodingRequest) -> TwoPassEligibilityCodingResponse:
    """
    Two-pass workflow:
      1) pass-1 eligibility without procedure codes (coverage gate)
      2) coding agent generates CDT/ICD
      3) pass-2 eligibility with CDT codes for per-procedure coverage
    """
    # Local imports keep this service bootable even if app modules are unavailable.
    from app.agents.coding_agent import run_coding_agent
    from app.config import get_settings as get_app_settings
    from app.integrations.supabase_client import create_supabase
    from app.schemas.coding import CodingAgentRequest

    settings = get_settings()

    # Pass 1: never depend on CDT list; this is the coverage gate.
    pass1_req = body.eligibility.model_copy(update={"cdt_codes": None})
    try:
        pass1 = run_eligibility_check_endpoint(pass1_req, settings=settings)
    except Layer1ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": e.code.value, "message": str(e), "layer": "layer1", "detail": e.detail},
        ) from e
    except (ValueError, RuntimeError) as e:
        raise sanitized_http_exception(
            400,
            public_message="Invalid eligibility request payload",
            log_message="two-pass pass1 failed",
            exc=e,
        ) from e
    except StediAPIError as e:
        raise _stedi_http_exception(e) from e

    pass1_status: str | None = None
    if pass1.get("cached"):
        rec = pass1.get("record") or {}
        pass1_status = rec.get("routing_status")
    else:
        primary = pass1.get("primary") or {}
        routing = primary.get("routing") or {}
        pass1_status = routing.get("status")

    if pass1_status != "CLEARED":
        return TwoPassEligibilityCodingResponse(
            pass1=pass1,
            halted_after_pass1=True,
            halt_reason=f"Pass 1 routing status is {pass1_status or 'unknown'}; coding and pass 2 skipped.",
        )

    # Coding runs only when pass 1 clears.
    app_settings = get_app_settings()
    app_supabase = create_supabase(app_settings)
    coding_out = run_coding_agent(
        app_settings,
        app_supabase,
        CodingAgentRequest(
            clinical_note=body.clinical_note,
            patient_age=body.patient_age,
            insurance=body.insurance,
        ),
    )
    coding_payload = coding_out.model_dump()

    # Pass 2: force real-time re-check with generated CDT codes.
    pass2_req = body.eligibility.model_copy(
        update={"cdt_codes": list(coding_out.cdt_codes), "trigger_event": TriggerEvent.PRE_APPOINTMENT}
    )
    try:
        pass2 = run_eligibility_check_endpoint(pass2_req, settings=settings)
    except Layer1ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": e.code.value, "message": str(e), "layer": "layer1", "detail": e.detail},
        ) from e
    except (ValueError, RuntimeError) as e:
        raise sanitized_http_exception(
            400,
            public_message="Invalid eligibility request payload",
            log_message="two-pass pass2 failed",
            exc=e,
        ) from e
    except StediAPIError as e:
        raise _stedi_http_exception(e) from e

    return TwoPassEligibilityCodingResponse(pass1=pass1, coding=coding_payload, pass2=pass2)


@app.post("/eligibility/batch")
def post_eligibility_batch(body: EligibilityBatchRequest) -> dict[str, Any]:
    """Batch sweep — Stedi batch endpoint only (never real-time per-item loop)."""
    if body.trigger_event is not TriggerEvent.BATCH_SWEEP:
        raise HTTPException(status_code=400, detail="trigger_event must be BATCH_SWEEP for this route")
    s = get_settings()
    items_payload: list[dict[str, Any]] = []
    for item in body.items:
        er = EligibilityRequest(
            patient_id=item.patient_id,
            first_name=item.first_name,
            last_name=item.last_name,
            dob=item.dob,
            subscriber_id=item.subscriber_id,
            primary_payer_id=item.primary_payer_id,
            cdt_codes=item.cdt_codes,
            trigger_event=TriggerEvent.BATCH_SWEEP,
        )
        try:
            er, _warnings = layer0_supabase_validation(er, settings=s)
        except Layer1ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail={"code": e.code.value, "message": str(e), "layer": "layer1", "detail": e.detail},
            ) from e
        payload = build_payload(er, s, trading_partner_service_id=item.primary_payer_id)
        payload["_patientId"] = str(item.patient_id)
        items_payload.append(payload)
    try:
        stedi_out = call_stedi_batch(items_payload, s)
    except StediAPIError as e:
        raise _stedi_http_exception(e) from e
    write_audit_event(
        patient_id=body.items[0].patient_id,
        event_type="ROUTING",
        detail={"batch_items": len(items_payload), "channel": "stedi_batch"},
        settings=s,
    )
    return {"batch_submitted": True, "stedi_response": stedi_out, "items_count": len(items_payload)}


@app.get("/eligibility/{patient_id}")
def get_latest_eligibility(patient_id: UUID) -> dict[str, Any]:
    s = get_settings()
    supabase = get_supabase(s)
    row = get_latest_eligibility_for_patient(supabase, patient_id)
    if not row:
        raise HTTPException(status_code=404, detail="no_eligibility_record")
    rid = row.get("id")
    procedures = list_procedure_estimates(supabase, UUID(str(rid))) if rid else []
    return {"record": row, "procedure_estimates": procedures}


@app.post("/eligibility/cob")
def post_eligibility_cob(body: CobRequest) -> dict[str, Any]:
    s = get_settings()
    supabase = get_supabase(s)
    p_row = get_eligibility_check_by_id(supabase, body.primary_eligibility_check_id)
    s_row = get_eligibility_check_by_id(supabase, body.secondary_eligibility_check_id)
    if not p_row or not s_row:
        raise HTTPException(status_code=404, detail="check_not_found")
    if not p_row.get("response_complete") or not s_row.get("response_complete"):
        raise HTTPException(
            status_code=422,
            detail="Both primary and secondary checks must be response_complete before COB",
        )

    p_est = list_procedure_estimates(supabase, body.primary_eligibility_check_id)
    s_est = list_procedure_estimates(supabase, body.secondary_eligibility_check_id)

    primary = {
        "response_complete": p_row.get("response_complete"),
        "procedure_estimates": p_est,
        "check_id": str(body.primary_eligibility_check_id),
    }
    secondary = {
        "response_complete": s_row.get("response_complete"),
        "procedure_estimates": s_est,
        "check_id": str(body.secondary_eligibility_check_id),
    }
    try:
        result = calculate_cob(primary, secondary)
    except ValueError as e:
        raise sanitized_http_exception(
            422,
            public_message="COB calculation failed",
            log_message="calculate_cob ValueError",
            exc=e,
        ) from e
    write_audit_event(
        patient_id=p_row.get("patient_id"),
        event_type="ROUTING",
        detail={"cob": "completed", "primary": str(body.primary_eligibility_check_id)},
        settings=s,
    )
    return result


@app.get("/eligibility/audit/{patient_id}")
def get_eligibility_audit(patient_id: UUID) -> dict[str, Any]:
    s = get_settings()
    supabase = get_supabase(s)
    entries = list_audit_for_patient(supabase, patient_id)
    return {"patient_id": str(patient_id), "entries": entries}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "eligibility_agent"}
