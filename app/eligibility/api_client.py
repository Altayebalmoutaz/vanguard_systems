"""Layer 2 — Stedi eligibility payload construction + HTTP client."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.models import EligibilityRequest, StediAPIError
from app.eligibility.stedi_errors import classify_aaa_response, should_retry_for_aaa

logger = logging.getLogger(__name__)


def build_payload(
    request: EligibilityRequest,
    settings: EligibilitySettings | None = None,
    *,
    trading_partner_service_id: str | None = None,
) -> dict[str, Any]:
    """
    Build JSON for POST .../eligibility/v3.

    Stedi expects `encounter.serviceTypeCodes` (not top-level) and dental CDT as ADA
    codes via `encounter.procedureCode` + `productOrServiceIDQualifier` AD, or
    `encounter.medicalProcedures` for multiple codes. See Stedi API reference.
    """
    s = settings or get_settings()
    tid = trading_partner_service_id or request.primary_payer_id
    codes = [c.strip().upper() for c in (request.cdt_codes or []) if c and str(c).strip()]

    encounter: dict[str, Any] = {"serviceTypeCodes": ["35"]}
    if len(codes) == 1:
        encounter["procedureCode"] = codes[0]
        encounter["productOrServiceIDQualifier"] = "AD"
    elif len(codes) > 1:
        encounter["medicalProcedures"] = [
            {"procedureCode": c, "productOrServiceIDQualifier": "AD"} for c in codes
        ]

    p_fn = (request.provider_first_name or "").strip()
    p_ln = (request.provider_last_name or "").strip()
    stedi_npi = (request.stedi_provider_npi or "").strip() or str(s.provider_npi or "").strip()
    if p_fn and p_ln:
        provider: dict[str, Any] = {
            "firstName": p_fn,
            "lastName": p_ln,
            "npi": stedi_npi,
        }
    else:
        org_nm = ((request.provider_organization_name or "").strip() or None) or s.provider_name
        provider = {
            "organizationName": org_nm,
            "npi": stedi_npi or s.provider_npi,
        }
        tax = (s.provider_tax_id or "").strip()
        if len(tax) == 9 and tax.isdigit():
            provider["taxId"] = tax

    subscriber = {
        "firstName": request.first_name,
        "lastName": request.last_name,
        "dateOfBirth": request.dob.strftime("%Y%m%d"),
        "memberId": request.subscriber_id,
    }
    dependents: list[dict[str, Any]] | None = None
    if request.patient_is_dependent:
        subscriber = {
            "firstName": request.subscriber_first_name,
            "lastName": request.subscriber_last_name,
            "dateOfBirth": request.subscriber_dob.strftime("%Y%m%d") if request.subscriber_dob else None,
            "memberId": request.subscriber_member_id or request.subscriber_id,
        }
        dependent: dict[str, Any] = {
            "firstName": request.first_name,
            "lastName": request.last_name,
            "dateOfBirth": request.dob.strftime("%Y%m%d"),
        }
        if request.dependent_member_id:
            dependent["memberId"] = request.dependent_member_id
        if request.dependent_relationship_code:
            dependent["individualRelationshipCode"] = request.dependent_relationship_code
        dependents = [dependent]

    body: dict[str, Any] = {
        "tradingPartnerServiceId": tid,
        "provider": provider,
        "subscriber": subscriber,
        "encounter": encounter,
    }
    if dependents:
        body["dependents"] = dependents
    if request.portal_password:
        body["portalPassword"] = request.portal_password
    return body


def build_payload_for_secondary(request: EligibilityRequest, settings: EligibilitySettings | None = None) -> dict[str, Any]:
    """Independent secondary payer payload (never merged with primary)."""
    if not request.secondary_payer_id:
        raise ValueError("secondary_payer_id required for secondary payload")
    return build_payload(request, settings, trading_partner_service_id=request.secondary_payer_id)


def _realtime_url(settings: EligibilitySettings) -> str:
    base = settings.stedi_base_url.rstrip("/")
    path = settings.stedi_eligibility_path if settings.stedi_eligibility_path.startswith("/") else f"/{settings.stedi_eligibility_path}"
    return f"{base}{path}"


def _batch_url(settings: EligibilitySettings) -> str:
    base = settings.stedi_manager_base_url.rstrip("/")
    p = settings.stedi_batch_eligibility_path
    p = p if p.startswith("/") else f"/{p}"
    return f"{base}{p}"


def _retry_sleep_seconds(attempt: int, settings: EligibilitySettings) -> float:
    """Exponential backoff with bounded jitter."""
    base = max(0.0, float(settings.stedi_retry_base_seconds))
    cap = max(base, float(settings.stedi_retry_max_seconds))
    jitter_cap = max(0.0, float(settings.stedi_retry_jitter_seconds))
    backoff = min(base * (2**attempt), cap)
    jitter = random.uniform(0.0, jitter_cap) if jitter_cap > 0 else 0.0
    return backoff + jitter


def _parse_json_dict_or_error(response: httpx.Response, *, source: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as e:
        raise StediAPIError(
            f"{source} returned non-JSON response",
            status_code=response.status_code,
            body=response.text[:4000],
        ) from e
    if not isinstance(payload, dict):
        raise StediAPIError(
            f"{source} returned non-object JSON payload",
            status_code=response.status_code,
            body=str(payload)[:4000],
        )
    return payload


def call_stedi(payload: dict[str, Any], settings: EligibilitySettings | None = None) -> dict[str, Any]:
    """
    POST real-time eligibility. Retries on 429 / 5xx with exponential backoff.
    """
    s = settings or get_settings()
    if not s.stedi_api_key:
        raise StediAPIError("STEDI_API_KEY is not configured", status_code=None, body=None)

    url = _realtime_url(s)
    headers = {"Authorization": f"Key {s.stedi_api_key}", "Content-Type": "application/json"}
    if s.stedi_test_header:
        headers["stedi-test"] = "true"
    last_exc: StediAPIError | None = None
    max_retries = max(1, int(s.stedi_max_retries))
    for attempt in range(max_retries):
        try:
            started = time.monotonic()
            with httpx.Client(timeout=float(s.stedi_timeout_seconds)) as client:
                r = client.post(url, json=payload, headers=headers)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = StediAPIError(
                    f"Stedi HTTP {r.status_code}", status_code=r.status_code, body=r.text[:2000]
                )
                if attempt < max_retries - 1:
                    wait = _retry_sleep_seconds(attempt, s)
                    logger.warning(
                        "Stedi eligibility retry %s/%s after HTTP %s (elapsed_ms=%s, sleep=%.3fs)",
                        attempt + 1,
                        max_retries,
                        r.status_code,
                        elapsed_ms,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                continue
            if r.status_code >= 400:
                raise StediAPIError(
                    f"Stedi eligibility failed: HTTP {r.status_code}",
                    status_code=r.status_code,
                    body=r.text[:4000],
                )
            payload = _parse_json_dict_or_error(r, source="Stedi eligibility")
            if should_retry_for_aaa(payload, http_status=r.status_code):
                aaa_actions = classify_aaa_response(payload, http_status=r.status_code)
                last_exc = StediAPIError(
                    "Stedi eligibility returned payer connectivity AAA",
                    status_code=r.status_code,
                    body=str({"aaa_actions": aaa_actions})[:2000],
                )
                if attempt < max_retries - 1:
                    wait = _retry_sleep_seconds(attempt, s)
                    logger.warning(
                        "Stedi eligibility retry %s/%s after AAA connectivity codes %s (elapsed_ms=%s, sleep=%.3fs)",
                        attempt + 1,
                        max_retries,
                        sorted({a["code"] for a in aaa_actions}),
                        elapsed_ms,
                        wait,
                    )
                    time.sleep(wait)
                    continue
            return payload
        except StediAPIError:
            raise
        except httpx.HTTPError as e:
            last_exc = StediAPIError(str(e), status_code=None, body=None)
            if attempt < max_retries - 1:
                wait = _retry_sleep_seconds(attempt, s)
                logger.warning(
                    "Stedi HTTP error on attempt %s/%s: %s (sleep=%.3fs)",
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                time.sleep(wait)
    if last_exc:
        raise last_exc
    raise StediAPIError("Stedi eligibility failed after retries", status_code=None, body=None)


def call_stedi_batch(
    items_payload: list[dict[str, Any]],
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    """
    POST batch eligibility sweep (separate manager host).
    """
    s = settings or get_settings()
    if not s.stedi_api_key:
        raise StediAPIError("STEDI_API_KEY is not configured", status_code=None, body=None)
    url = _batch_url(s)
    headers = {"Authorization": f"Key {s.stedi_api_key}", "Content-Type": "application/json"}
    if s.stedi_test_header:
        headers["stedi-test"] = "true"
    body = {"items": items_payload}
    with httpx.Client(timeout=float(s.stedi_batch_timeout_seconds)) as client:
        r = client.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise StediAPIError(
            f"Stedi batch failed: HTTP {r.status_code}",
            status_code=r.status_code,
            body=r.text[:4000],
        )
    return _parse_json_dict_or_error(r, source="Stedi batch eligibility")
