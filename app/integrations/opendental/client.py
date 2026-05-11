"""OpenDental REST client for eligibility integration."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.eligibility.config import EligibilitySettings
from app.integrations.opendental.errors import OpenDentalAPIError, OpenDentalConfigError
from app.integrations.opendental.models import (
    ODCarrier,
    ODInsuranceRow,
    ODInsVerifyCreate,
    ODInsVerifyResponse,
    ODPatient,
)

logger = logging.getLogger(__name__)


class OpenDentalClient:
    def __init__(
        self,
        *,
        base_url: str,
        developer_key: str,
        customer_key: str,
        timeout_seconds: float,
        replay_dir: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.developer_key = developer_key.strip()
        self.customer_key = customer_key.strip()
        self.timeout_seconds = timeout_seconds
        self.replay_dir = Path(replay_dir).resolve() if replay_dir else None

        if not self.developer_key or not self.customer_key:
            raise OpenDentalConfigError("Missing OpenDental developer/customer key")
        if not self.base_url.startswith(("http://", "https://")):
            raise OpenDentalConfigError("OpenDental base URL must start with http:// or https://")

    @classmethod
    def from_settings(cls, settings: EligibilitySettings) -> "OpenDentalClient":
        return cls(
            base_url=settings.opendental_base_url,
            developer_key=settings.opendental_developer_key,
            customer_key=settings.opendental_customer_key,
            timeout_seconds=settings.opendental_timeout_seconds,
            replay_dir=settings.opendental_replay_dir or None,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"ODFHIR {self.developer_key}/{self.customer_key}",
            "Content-Type": "application/json",
        }

    def _fixture_path(self, stem: str) -> Path:
        if self.replay_dir is None:
            raise OpenDentalConfigError("Replay mode not enabled")
        return self.replay_dir / f"{stem}.json"

    def _read_fixture(self, stem: str) -> object:
        p = self._fixture_path(stem)
        if not p.exists():
            raise OpenDentalAPIError(f"Replay fixture not found: {p}")
        return json.loads(p.read_text(encoding="utf-8"))

    def _get_json(self, path: str) -> object:
        url = urljoin(self.base_url, path.lstrip("/"))
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.get(url, headers=self._headers())
        if resp.status_code >= 400:
            raise OpenDentalAPIError(
                f"OpenDental GET failed for {path}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise OpenDentalAPIError("OpenDental response was not valid JSON", body=resp.text) from exc

    def _put_json(self, path: str, payload: dict[str, object]) -> object:
        url = urljoin(self.base_url, path.lstrip("/"))
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.put(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise OpenDentalAPIError(
                f"OpenDental PUT failed for {path}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise OpenDentalAPIError("OpenDental response was not valid JSON", body=resp.text) from exc

    def get_patient(self, pat_num: int) -> ODPatient:
        if self.replay_dir:
            payload = self._read_fixture(f"patient_{pat_num}")
        else:
            payload = self._get_json(f"/patients/{pat_num}")
        return ODPatient.model_validate(payload)

    def get_patient_insurance(self, pat_num: int) -> list[ODInsuranceRow]:
        if self.replay_dir:
            payload = self._read_fixture(f"familymodules_{pat_num}")
        else:
            payload = self._get_json(f"/familymodules/{pat_num}/Insurance")
        if not isinstance(payload, list):
            raise OpenDentalAPIError("OpenDental insurance payload was not a list")
        return [ODInsuranceRow.model_validate(row) for row in payload]

    def get_carrier(self, carrier_num: int) -> ODCarrier:
        if self.replay_dir:
            payload = self._read_fixture(f"carrier_{carrier_num}")
        else:
            payload = self._get_json(f"/carriers/{carrier_num}")
        return ODCarrier.model_validate(payload)

    def create_insverify(self, payload: ODInsVerifyCreate) -> ODInsVerifyResponse:
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping PUT /insverifies")
            return ODInsVerifyResponse(
                InsVerifyNum=0,
                DateLastVerified=date.today(),
                VerifyType=payload.VerifyType,
                FKey=payload.FKey,
                Note=payload.Note,
            )
        out = self._put_json("/insverifies", payload.model_dump(mode="json", exclude_none=True))
        return ODInsVerifyResponse.model_validate(out)

