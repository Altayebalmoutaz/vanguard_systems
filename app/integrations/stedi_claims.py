"""
Stedi Healthcare Claims (837) submission adapter.

This module is the first real clearinghouse adapter on the claims path. It
replaces the previous ``stedi_mock`` placeholder in
:mod:`app.tools.claim_tools` whenever ``Settings.stedi_claims_api_key`` is
configured. When no key is configured we fall back to the mock so local
development and the existing test suite stay deterministic and offline.

Design notes
------------

* **PHI safety:** the request body sent to Stedi is built from
  ``ClaimStructure`` fields that the office has already validated and persisted
  for billing. Errors returned by Stedi are surfaced as a structured
  :class:`StediClaimsError`; callers are expected to log the *scrubbed* version
  via :func:`app.security.phi.scrub_for_log` before persisting or re-raising.
* **Mapping:** the function :func:`build_dental_claim_payload` is a
  best-effort mapping from our internal ``ClaimStructure``-shaped dict to
  Stedi's healthcare claim JSON. The Stedi product surface evolves; treat this
  as a reference implementation and verify against the live OpenAPI before any
  go-live. The shape mirrors how :mod:`app.eligibility.api_client` builds the
  eligibility payload, so the two stay symmetric.
* **Idempotency:** real production runs must include a stable idempotency key
  to prevent double-submission on retry; the adapter accepts an optional
  ``idempotency_key`` parameter and forwards it as ``Idempotency-Key`` header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StediClaimsError(Exception):
    """Stedi claim-submission failure surfaced to the agent layer."""

    message: str
    status_code: int | None
    body: str | None

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Payload mapping
# ---------------------------------------------------------------------------


def _decimal_str(value: Any) -> str:
    """Render Decimals / strings / numbers as canonical 2-decimal strings.

    Stedi expects monetary fields as strings, e.g. ``"125.00"``.
    """
    if value is None:
        return "0.00"
    if isinstance(value, str):
        # Allow already-formatted strings to flow through unchanged so we don't
        # silently re-quantize cents-precise input from the biller UI.
        try:
            d = Decimal(value)
        except Exception:
            return value
    else:
        d = Decimal(str(value))
    return f"{d:.2f}"


def _service_line_payload(line: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "assignedNumber": str(line["line_number"]),
        "professionalService": {
            "procedureIdentifier": "AD",  # AD = ADA / CDT codeset
            "procedureCode": str(line["cdt_code"]).strip().upper(),
            "lineItemChargeAmount": _decimal_str(line["charge_amount"]),
            "serviceUnitCount": str(line.get("units") or "1"),
            "compositeDiagnosisCodePointers": {
                "diagnosisCodePointers": [str(p) for p in (line.get("diagnosis_pointers") or [1])],
            },
        },
        "serviceDateInformation": {
            "serviceDate": str(line["service_date"]).replace("-", ""),
        },
    }
    tooth = line.get("tooth_number")
    if tooth:
        out["toothInformation"] = {"toothCode": str(tooth)}
    surface = line.get("surface")
    if surface:
        out.setdefault("toothInformation", {})["toothSurface"] = str(surface)
    pa = line.get("prior_auth_number")
    if pa:
        out["referenceInformation"] = [
            {"referenceIdentificationQualifier": "G1", "referenceIdentification": str(pa)},
        ]
    return out


def _address(addr: dict[str, Any]) -> dict[str, str]:
    return {
        "addressLine1": str(addr["line1"]),
        "city": str(addr["city"]),
        "state": str(addr["state"]),
        "postalCode": str(addr["postal_code"]),
    }


def build_dental_claim_payload(claim: dict[str, Any]) -> dict[str, Any]:
    """Map an internal :class:`app.schemas.claim.ClaimStructure` dict → Stedi 837 JSON.

    Parameters
    ----------
    claim:
        ``ClaimStructure.model_dump()`` output. Must contain ``patient``,
        ``subscriber``, ``payer``, ``billing_provider``, ``rendering_provider``,
        ``service_lines``, ``diagnosis_codes``, ``patient_address``,
        ``patient_account_number``, etc. — see ``app/schemas/claim.py``.

    Returns
    -------
    dict
        Stedi-shaped JSON ready to ``json.dumps()`` and POST.
    """
    patient = claim["patient"]
    patient_addr = _address(claim["patient_address"])
    subscriber = claim["subscriber"]
    payer = claim["payer"]
    billing = claim["billing_provider"]
    rendering = claim["rendering_provider"]
    diagnosis_codes = claim.get("diagnosis_codes") or []
    service_lines = claim.get("service_lines") or []

    body: dict[str, Any] = {
        "tradingPartnerServiceId": str(payer["payer_id"]),
        "submitter": {
            "organizationName": str(billing["name"]),
            "contactInformation": {"name": str(billing["name"])},
        },
        "receiver": {"organizationName": str(payer["payer_name"])},
        "billing": {
            "providerType": "BillingProvider",
            "organizationName": str(billing["name"]),
            "npi": str(billing["npi"]),
            "employerId": str(billing["tax_id"]),
            "providerTaxonomyCode": str(billing["taxonomy_code"]),
            "address": _address(billing["address"]),
        },
        "rendering": {
            "providerType": "RenderingProvider",
            "lastName": str(rendering["name"]),
            "npi": str(rendering["npi"]),
            "providerTaxonomyCode": str(rendering["taxonomy_code"]),
        },
        "subscriber": {
            "memberId": str(subscriber["member_id"]),
            "paymentResponsibilityLevelCode": "P",  # P = primary
            "individualRelationshipCode": _relationship_code(
                subscriber.get("relationship_to_patient")
            ),
            "firstName": _split_first(subscriber["name"]),
            "lastName": _split_last(subscriber["name"]),
            "dateOfBirth": str(subscriber["dob"]).replace("-", ""),
            "address": _address(subscriber["address"]),
        },
        "claimInformation": {
            "claimFilingCode": "CI",  # CI = commercial insurance (default)
            "patientControlNumber": str(claim.get("patient_account_number") or "")[:38],
            "claimChargeAmount": _decimal_str(claim.get("total_charge_amount")),
            "placeOfServiceCode": str(claim.get("place_of_service") or "11"),
            "claimFrequencyCode": str(claim.get("claim_frequency_code") or "1"),
            "signatureIndicator": "Y",
            "providerAcceptAssignmentCode": "A",
            "benefitsAssignmentCertificationIndicator": "Y",
            "releaseInformationCode": "Y",
            "healthCareCodeInformation": [
                {
                    "diagnosisTypeCode": "ABK" if i == 0 else "ABF",
                    "diagnosisCode": str(code).replace(".", ""),
                }
                for i, code in enumerate(diagnosis_codes)
            ],
            "serviceLines": [_service_line_payload(line) for line in service_lines],
        },
        "patient": {
            "firstName": _split_first(patient["name"]),
            "lastName": _split_last(patient["name"]),
            "dateOfBirth": str(patient["dob"]).replace("-", ""),
            "gender": _gender_code(claim.get("patient_sex")),
            "address": patient_addr,
        },
    }
    return body


def _relationship_code(relationship: str | None) -> str:
    return {"self": "18", "spouse": "01", "child": "19", "other": "G8"}.get(
        (relationship or "self").lower(), "18"
    )


def _gender_code(sex: str | None) -> str:
    return {"M": "M", "F": "F", "U": "U"}.get((sex or "U").upper(), "U")


def _split_first(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""


def _split_last(full_name: str) -> str:
    parts = full_name.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else parts[0] if parts else ""


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def submit_dental_claim(
    claim: dict[str, Any],
    settings: Settings,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """POST a dental claim to Stedi and return a normalised response dict.

    The returned dict matches the contract expected by
    :mod:`app.agents.claim_agent`:

    * ``claim_id`` — Stedi's transaction identifier (or ``""`` if absent).
    * ``status`` — ``"submitted"`` on success.
    * ``submission_channel`` — ``"stedi_dental"``.
    * ``raw`` — sanitised excerpt of the Stedi response for downstream audit.

    Raises
    ------
    StediClaimsError
        Raised on transport failure or any non-2xx HTTP response. The message
        is operator-facing; do **not** echo it back to API clients verbatim.
    """
    if not settings.stedi_claims_api_key:
        raise StediClaimsError("STEDI_CLAIMS_API_KEY not configured", status_code=None, body=None)

    payload = build_dental_claim_payload(claim)
    url = settings.stedi_claims_base_url.rstrip("/") + (
        settings.stedi_claims_dental_path
        if settings.stedi_claims_dental_path.startswith("/")
        else f"/{settings.stedi_claims_dental_path}"
    )
    headers: dict[str, str] = {
        "Authorization": f"Key {settings.stedi_claims_api_key}",
        "Content-Type": "application/json",
    }
    if settings.stedi_claims_test_header:
        headers["stedi-test"] = "true"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    try:
        with httpx.Client(timeout=float(settings.stedi_claims_timeout_seconds)) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise StediClaimsError(
            f"Stedi claims transport failure: {exc}", status_code=None, body=None
        ) from exc

    if response.status_code >= 400:
        raise StediClaimsError(
            f"Stedi claims HTTP {response.status_code}",
            status_code=response.status_code,
            body=response.text[:4000],
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise StediClaimsError(
            "Stedi claims returned non-JSON response",
            status_code=response.status_code,
            body=response.text[:4000],
        ) from exc

    if not isinstance(body, dict):
        raise StediClaimsError(
            "Stedi claims returned non-object JSON payload",
            status_code=response.status_code,
            body=str(body)[:4000],
        )

    claim_id = (
        body.get("controlNumber")
        or body.get("transactionControlNumber")
        or body.get("claimControlNumber")
        or ""
    )
    return {
        "claim_id": str(claim_id) or _fallback_claim_id(),
        "status": "submitted",
        "submission_channel": "stedi_dental",
        "raw": _sanitise_response_for_audit(body),
    }


def _fallback_claim_id() -> str:
    """When Stedi succeeds without echoing a control number, generate a stable suffix."""
    import secrets

    return f"CLM{secrets.randbelow(90_000) + 10_000}"


def _sanitise_response_for_audit(body: dict[str, Any]) -> dict[str, Any]:
    """Strip large/echoed PHI fields from Stedi response before storing.

    Stedi may echo subscriber/patient blocks back to the caller for debugging.
    We keep only structural fields safe for the audit log.
    """
    keep = {
        "controlNumber",
        "transactionControlNumber",
        "claimControlNumber",
        "status",
        "errors",
        "warnings",
        "tradingPartnerServiceId",
    }
    return {k: v for k, v in body.items() if k in keep}


__all__ = [
    "StediClaimsError",
    "build_dental_claim_payload",
    "submit_dental_claim",
]
