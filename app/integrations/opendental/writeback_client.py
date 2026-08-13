"""HTTP client for the standalone OpenDental write-back service.

Enabled when Settings.odwb_service_url is set. Default is unset (in-process).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class OpenDentalWritebackServiceError(RuntimeError):
    """Transport or non-2xx failure calling the OD write-back service."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def call_opendental_writeback_service(
    settings: Settings,
    *,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST /v1/writeback on the OD write-back service. Raises on transport/5xx."""
    base = (settings.odwb_service_url or "").rstrip("/")
    if not base:
        raise OpenDentalWritebackServiceError("ODWB_SERVICE_URL is not configured")
    api_key = (settings.odwb_api_key or "").strip()
    if not api_key:
        raise OpenDentalWritebackServiceError("ODWB_API_KEY is not configured")

    url = f"{base}/v1/writeback"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = float(settings.odwb_timeout_seconds)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise OpenDentalWritebackServiceError(
            f"OD writeback service transport error: {type(exc).__name__}: {exc}"
        ) from exc

    if resp.status_code >= 500:
        raise OpenDentalWritebackServiceError(
            f"OD writeback service error {resp.status_code}: {resp.text[:400]}",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        raise OpenDentalWritebackServiceError(
            f"OD writeback service rejected request {resp.status_code}: {resp.text[:400]}",
            status_code=resp.status_code,
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise OpenDentalWritebackServiceError(
            "OD writeback service returned non-JSON body"
        ) from exc
    if not isinstance(data, dict):
        raise OpenDentalWritebackServiceError("OD writeback service returned non-object JSON")
    return data
