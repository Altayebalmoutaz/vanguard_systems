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
    ODBenefit,
    ODBenefitCreate,
    ODBenefitUpdate,
    ODCarrier,
    ODClaimProcInsAdjust,
    ODCommlogCreate,
    ODCommlogResponse,
    ODCovCat,
    ODInsSubBenefitNotesUpdate,
    ODInsSubSubscNoteUpdate,
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

    def _send_json(self, method: str, path: str, payload: dict[str, object]) -> object:
        """Send a JSON request tolerating empty/non-JSON bodies (OD often returns bare '200 OK')."""
        url = urljoin(self.base_url, path.lstrip("/"))
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.request(method, url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise OpenDentalAPIError(
                f"OpenDental {method} failed for {path}",
                status_code=resp.status_code,
                body=resp.text,
            )
        text = (resp.text or "").strip()
        if not text:
            return {}
        try:
            return resp.json()
        except Exception:
            return {"_raw": text}

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

    def update_inssub_benefit_notes(
        self, ins_sub_num: int, plan_num: int, benefit_notes: str
    ) -> dict[str, object]:
        """PUT /inssubs/{InsSubNum} - primary structured eligibility storage (BenefitNotes)."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping PUT /inssubs/%s", ins_sub_num)
            return {
                "InsSubNum": ins_sub_num,
                "PlanNum": plan_num,
                "BenefitNotes": benefit_notes,
                "_replay": True,
            }
        payload = ODInsSubBenefitNotesUpdate(PlanNum=plan_num, BenefitNotes=benefit_notes).model_dump(
            mode="json"
        )
        out = self._send_json("PUT", f"/inssubs/{ins_sub_num}", payload)
        return out if isinstance(out, dict) else {"response": out}

    def update_inssub_subscriber_note(
        self, ins_sub_num: int, plan_num: int, subscriber_note: str
    ) -> dict[str, object]:
        """PUT /inssubs/{InsSubNum} - SubscNote (renders bold-red on the insurance grid)."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping PUT /inssubs/%s SubscNote", ins_sub_num)
            return {
                "InsSubNum": ins_sub_num,
                "PlanNum": plan_num,
                "SubscNote": subscriber_note,
                "_replay": True,
            }
        payload = ODInsSubSubscNoteUpdate(PlanNum=plan_num, SubscNote=subscriber_note).model_dump(
            mode="json"
        )
        out = self._send_json("PUT", f"/inssubs/{ins_sub_num}", payload)
        return out if isinstance(out, dict) else {"response": out}

    def create_commlog(
        self,
        pat_num: int,
        note: str,
        *,
        comm_type: str = "Insurance",
        mode: str = "None",
        sent_or_received: str = "Neither",
    ) -> ODCommlogResponse:
        """POST /commlogs - human-readable eligibility summary for the front desk."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping POST /commlogs")
            return ODCommlogResponse(CommlogNum=0, PatNum=pat_num, Note=note)
        payload = ODCommlogCreate(
            PatNum=pat_num,
            Note=note,
            commType=comm_type,
            Mode_=mode,
            SentOrReceived=sent_or_received,
        ).model_dump(mode="json", exclude_none=True)
        out = self._send_json("POST", "/commlogs", payload)
        if isinstance(out, dict) and "_raw" not in out:
            try:
                return ODCommlogResponse.model_validate(out)
            except Exception:  # pragma: no cover - defensive
                pass
        return ODCommlogResponse(PatNum=pat_num, Note=note)

    def put_claimproc_insadjust(
        self,
        pat_plan_num: int,
        *,
        ins_used: float | None = None,
        deductible_used: float | None = None,
        on_date: date | None = None,
    ) -> dict[str, object]:
        """PUT /claimprocs/InsAdjust - Phase 2 financial sync of used insurance/deductible."""
        if ins_used is None and deductible_used is None:
            raise OpenDentalAPIError("InsAdjust requires insUsed or deductibleUsed")
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping PUT /claimprocs/InsAdjust")
            return {
                "PatPlanNum": pat_plan_num,
                "insUsed": ins_used,
                "deductibleUsed": deductible_used,
                "_replay": True,
            }
        payload = ODClaimProcInsAdjust(
            PatPlanNum=pat_plan_num,
            insUsed=None if ins_used is None else f"{ins_used:.2f}",
            deductibleUsed=None if deductible_used is None else f"{deductible_used:.2f}",
            date=(on_date or date.today()).isoformat(),
        ).model_dump(mode="json", exclude_none=True)
        out = self._send_json("PUT", "/claimprocs/InsAdjust", payload)
        return out if isinstance(out, dict) else {"response": out}

    def get_covcats(self) -> list[ODCovCat]:
        """GET /covcats - coverage categories (used to map EbenefitCat -> CovCatNum)."""
        if self.replay_dir:
            payload = self._read_fixture("covcats")
        else:
            payload = self._get_json("/covcats")
        if not isinstance(payload, list):
            raise OpenDentalAPIError("OpenDental covcats payload was not a list")
        return [ODCovCat.model_validate(row) for row in payload]

    def get_benefits(self, plan_num: int) -> list[ODBenefit]:
        """GET /benefits?PlanNum= - existing structured benefit-grid rows for a plan."""
        if self.replay_dir:
            payload = self._read_fixture(f"benefits_plan_{plan_num}")
        else:
            payload = self._get_json(f"/benefits?PlanNum={plan_num}")
        if not isinstance(payload, list):
            raise OpenDentalAPIError("OpenDental benefits payload was not a list")
        return [ODBenefit.model_validate(row) for row in payload]

    def create_benefit(self, payload: ODBenefitCreate) -> ODBenefit:
        """POST /benefits - create a new structured benefit row."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping POST /benefits")
            return ODBenefit(BenefitNum=0, **payload.model_dump(exclude_none=True))
        out = self._send_json("POST", "/benefits", payload.model_dump(mode="json", exclude_none=True))
        if isinstance(out, dict) and "_raw" not in out:
            try:
                return ODBenefit.model_validate(out)
            except Exception:  # pragma: no cover - defensive
                pass
        return ODBenefit(BenefitNum=0, **payload.model_dump(exclude_none=True))

    def update_benefit(self, benefit_num: int, payload: ODBenefitUpdate) -> ODBenefit:
        """PUT /benefits/{BenefitNum} - update an existing benefit row's value fields."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping PUT /benefits/%s", benefit_num)
            return ODBenefit(BenefitNum=benefit_num, **payload.model_dump(exclude_none=True))
        out = self._send_json(
            "PUT", f"/benefits/{benefit_num}", payload.model_dump(mode="json", exclude_none=True)
        )
        if isinstance(out, dict) and "_raw" not in out:
            try:
                return ODBenefit.model_validate(out)
            except Exception:  # pragma: no cover - defensive
                pass
        return ODBenefit(BenefitNum=benefit_num, **payload.model_dump(exclude_none=True))

