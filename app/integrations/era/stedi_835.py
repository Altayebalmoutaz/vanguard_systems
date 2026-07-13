"""
Stedi Healthcare Remittance (835) adapter — sandbox skeleton.

Wave 3C introduces a narrow adapter boundary so denial / ERA workflows can
eventually ingest real clearinghouse remittances without rewriting
:mod:`app.tools.denial_tools`.

Today:
  * **JSON sandbox** — ``Stedi835SandboxAdapter`` accepts UTF-8 JSON (or a
    pre-parsed dict via :func:`parse_stedi_835_json`) and delegates normalization
    to :func:`app.tools.denial_tools.parse_era_tool`.
  * **Raw X12 835** — intentionally **not** implemented. Production will likely
    fetch Stedi's JSON translation of an 835 or parse EDI via their API; a local
    X12 parser can be added behind the same :class:`EraRemittanceAdapter`
    protocol when requirements are frozen.

No production Stedi API keys are read or required by this module.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from app.tools.denial_tools import parse_era_tool

# Stedi claim-status codes (835 CLP02) commonly seen in sandbox samples.
_CLP_STATUS_TO_ERA: dict[str, str] = {
    "1": "paid",
    "2": "partial",
    "3": "partial",
    "4": "denied",
    "19": "denied",
    "22": "partial",
}


class Stedi835X12NotImplementedError(NotImplementedError):
    """Raised when raw X12 835 input is supplied before the EDI path exists."""


@runtime_checkable
class EraRemittanceAdapter(Protocol):
    """Clearinghouse remittance adapter contract (835 / ERA)."""

    def parse_remittance(self, raw: bytes | str) -> dict[str, Any]:
        """Parse a remittance payload into ``{status, reason}``."""
        ...


def _normalize_claim_status(raw_status: Any) -> str:
    token = str(raw_status or "paid").lower().strip()
    if token in ("paid", "denied", "partial"):
        return token
    mapped = _CLP_STATUS_TO_ERA.get(token)
    if mapped:
        return mapped
    return "paid"


def parse_stedi_835_json(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Stedi 835 sandbox JSON to a mock-ERA dict ``{status, reason}``.

    Supported shapes (verify against live Stedi OpenAPI before go-live):

    1. Direct mock slice::

         {"status": "denied", "reason": "missing_xray"}

    2. Pipeline / agent request wrapper::

         {"claim_id": "CLM1", "mock_era": {"status": "partial", "reason": "..."}}

    3. Stedi stub remittance (first claim wins)::

         {
           "transactionId": "txn-123",
           "claims": [{
             "patientControlNumber": "CLM123",
             "claimStatus": "4",
             "denialReason": "missing_xray"
           }]
         }
    """
    if "mock_era" in payload and isinstance(payload["mock_era"], dict):
        inner = payload["mock_era"]
        return {
            "status": _normalize_claim_status(inner.get("status")),
            "reason": str(inner.get("reason") or "").strip(),
        }

    if "status" in payload:
        return {
            "status": _normalize_claim_status(payload.get("status")),
            "reason": str(payload.get("reason") or "").strip(),
        }

    claims = payload.get("claims")
    if isinstance(claims, list) and claims:
        claim = claims[0]
        if isinstance(claim, dict):
            status_raw = claim.get("claimStatus") or claim.get("status")
            reason = claim.get("denialReason") or claim.get("reason") or ""
            return {
                "status": _normalize_claim_status(status_raw),
                "reason": str(reason).strip(),
            }

    return {"status": "paid", "reason": ""}


def _looks_like_x12_835(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.startswith("ISA"):
        return True
    upper = stripped.upper()
    return upper.startswith("ST*835") or "~ST*835*" in upper


def get_default_era_adapter() -> Stedi835SandboxAdapter:
    """Return the sandbox ERA adapter (production Stedi API wiring lands later)."""
    return Stedi835SandboxAdapter()


def parse_remittance_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a remittance dict through the default adapter → ``{status, reason}``."""
    return get_default_era_adapter().parse_remittance(json.dumps(payload))


class Stedi835SandboxAdapter:
    """
    Sandbox-only Stedi 835 adapter.

    Accepts UTF-8 JSON remittance bodies. Raw X12 835 EDI will be handled by a
    future production adapter once Stedi response shapes and persistence are
    finalized.
    """

    def parse_remittance(self, raw: bytes | str) -> dict[str, Any]:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        stripped = text.strip()
        if not stripped:
            raise ValueError("Empty remittance payload")

        if _looks_like_x12_835(stripped):
            raise Stedi835X12NotImplementedError(
                "Raw X12 835 EDI parsing is not implemented. "
                "Use Stedi sandbox JSON remittance responses until the X12 path lands."
            )

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("Expected UTF-8 JSON remittance payload in sandbox mode") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Stedi 835 JSON root must be an object")

        normalized = parse_stedi_835_json(parsed)
        return parse_era_tool(normalized)
