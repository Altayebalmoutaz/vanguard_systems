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
    ODClaimPayment,
    ODClaimProcInsAdjust,
    ODCommlogCreate,
    ODCommlogResponse,
    ODCovCat,
    ODDeposit,
    ODEtransMessageText,
    ODInsHistCreate,
    ODInsHistRow,
    ODInsSubBenefitNotesUpdate,
    ODInsSubSubscNoteUpdate,
    ODInsuranceRow,
    ODInsVerifyCreate,
    ODInsVerifyResponse,
    ODPatient,
    ODProcedureCode,
    ODProcedureLog,
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
    def from_settings(cls, settings: EligibilitySettings) -> OpenDentalClient:
        return cls(
            base_url=settings.opendental_base_url,
            developer_key=settings.opendental_developer_key,
            customer_key=settings.opendental_customer_key,
            timeout_seconds=settings.opendental_timeout_seconds,
            replay_dir=settings.opendental_replay_dir or None,
        )

    @classmethod
    def from_connection(
        cls,
        connection: dict[str, object],
        *,
        settings: EligibilitySettings,
    ) -> OpenDentalClient:
        """Build a per-clinic client from an ``rcm.opendental_connections`` row.

        The Developer key is our single global secret; the clinic Customer key is
        resolved from the env var named by ``customer_key_ref`` (falling back to
        the global ``OPENDENTAL_CUSTOMER_KEY`` for single-clinic setups).
        """
        from app.integrations.opendental.connections_store import resolve_customer_key

        customer_key = resolve_customer_key(
            str(connection.get("customer_key_ref") or "") or None,
            fallback=settings.opendental_customer_key,
        )
        if not customer_key:
            raise OpenDentalConfigError(
                f"No OpenDental customer key configured for practice "
                f"{connection.get('practice_id')!r} (customer_key_ref="
                f"{connection.get('customer_key_ref')!r})"
            )
        return cls(
            base_url=str(connection.get("base_url") or "") or settings.opendental_base_url,
            developer_key=settings.opendental_developer_key,
            customer_key=customer_key,
            timeout_seconds=settings.opendental_timeout_seconds,
        )

    def check_connection(self) -> dict[str, object]:
        """Lightweight connectivity/auth probe (used by the dashboard Test button)."""
        try:
            # /covcats is small, read-only, and requires valid keys.
            payload = (
                self._read_fixture("covcats") if self.replay_dir else self._get_json("/covcats")
            )
            count = len(payload) if isinstance(payload, list) else None
            return {"ok": True, "covcats_count": count}
        except OpenDentalAPIError as exc:
            detail = (exc.body or "").strip() or str(exc)
            if len(detail) > 400:
                detail = detail[:400] + "…"
            return {"ok": False, "status_code": exc.status_code, "error": detail}
        except Exception as exc:  # network/DNS/TLS failures
            return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}"}

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

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """HTTP call with transport failures mapped to ``OpenDentalAPIError``."""
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                return client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.TimeoutException as exc:
            raise OpenDentalAPIError(
                f"OpenDental {method} timed out for {path}",
                status_code=504,
                body="504 Gateway Time-out: The server didn't respond in time.",
            ) from exc
        except httpx.TransportError as exc:
            raise OpenDentalAPIError(
                f"OpenDental {method} transport failed for {path}: {exc}",
                status_code=None,
                body=str(exc),
            ) from exc

    def _get_json(self, path: str) -> object:
        resp = self._request("GET", path)
        if resp.status_code >= 400:
            raise OpenDentalAPIError(
                f"OpenDental GET failed for {path}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise OpenDentalAPIError(
                "OpenDental response was not valid JSON", body=resp.text
            ) from exc

    def _put_json(self, path: str, payload: dict[str, object]) -> object:
        resp = self._request("PUT", path, json=payload)
        if resp.status_code >= 400:
            raise OpenDentalAPIError(
                f"OpenDental PUT failed for {path}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise OpenDentalAPIError(
                "OpenDental response was not valid JSON", body=resp.text
            ) from exc

    def _send_json(self, method: str, path: str, payload: dict[str, object]) -> object:
        """Send a JSON request tolerating empty/non-JSON bodies (OD often returns bare '200 OK')."""
        resp = self._request(method, path, json=payload)
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

    def get_procedurelogs_for_appointment(self, apt_num: int) -> list[ODProcedureLog]:
        """GET /procedurelogs?AptNum= — returns [] on error (poller-friendly)."""
        try:
            if self.replay_dir:
                payload = self._read_fixture(f"procedurelogs_apt_{apt_num}")
            else:
                payload = self._get_json(f"/procedurelogs?AptNum={int(apt_num)}")
            if not isinstance(payload, list):
                logger.warning("OpenDental procedurelogs for AptNum=%s was not a list", apt_num)
                return []
            return [ODProcedureLog.model_validate(row) for row in payload]
        except Exception as exc:
            logger.warning(
                "OpenDental procedurelogs AptNum=%s failed: %s: %s",
                apt_num,
                type(exc).__name__,
                exc,
            )
            return []

    def get_procedure_catalog(self) -> list[ODProcedureCode]:
        """GET /procedurecodes — the full OD procedure code dictionary.

        Read-only, no PHI (a code catalog). Returns [] on error so a one-time
        reference import can degrade gracefully. Supports ``replay_dir`` fixtures.
        """
        try:
            if self.replay_dir:
                payload = self._read_fixture("procedurecodes")
            else:
                payload = self._get_json("/procedurecodes")
            if not isinstance(payload, list):
                logger.warning("OpenDental procedurecodes payload was not a list")
                return []
            return [ODProcedureCode.model_validate(row) for row in payload]
        except Exception as exc:
            logger.warning(
                "OpenDental procedurecodes fetch failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return []

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
        payload = ODInsSubBenefitNotesUpdate(
            PlanNum=plan_num, BenefitNotes=benefit_notes
        ).model_dump(mode="json")
        out = self._send_json("PUT", f"/inssubs/{ins_sub_num}", payload)
        return out if isinstance(out, dict) else {"response": out}

    def update_inssub_subscriber_note(
        self, ins_sub_num: int, plan_num: int, subscriber_note: str
    ) -> dict[str, object]:
        """PUT /inssubs/{InsSubNum} - SubscNote (renders bold-red on the insurance grid)."""
        if self.replay_dir:
            logger.warning(
                "OpenDental replay mode active: skipping PUT /inssubs/%s SubscNote", ins_sub_num
            )
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

    def get_insurance_history(self, pat_num: int, ins_sub_num: int) -> list[ODInsHistRow]:
        """GET /procedurelogs/InsuranceHistory?PatNum=&InsSubNum=."""
        path = f"/procedurelogs/InsuranceHistory?PatNum={pat_num}&InsSubNum={ins_sub_num}"
        if self.replay_dir:
            payload = self._read_fixture(f"inshist_{pat_num}_{ins_sub_num}")
        else:
            payload = self._get_json(path)
        if not isinstance(payload, list):
            raise OpenDentalAPIError("OpenDental InsuranceHistory payload was not a list")
        return [ODInsHistRow.model_validate(row) for row in payload]

    def create_insurance_history(self, payload: ODInsHistCreate) -> dict[str, object]:
        """POST /procedurelogs/InsuranceHistory - creates EO proc + InsHist like the Hist UI."""
        if self.replay_dir:
            logger.warning("OpenDental replay mode active: skipping POST InsuranceHistory")
            return {**payload.model_dump(mode="json"), "_replay": True}
        out = self._send_json(
            "POST",
            "/procedurelogs/InsuranceHistory",
            payload.model_dump(mode="json"),
        )
        return out if isinstance(out, dict) else {"response": out}

    def get_covcats(self) -> list[ODCovCat]:
        """GET /covcats - coverage categories (used to map EbenefitCat -> CovCatNum)."""
        payload = self._read_fixture("covcats") if self.replay_dir else self._get_json("/covcats")
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
        out = self._send_json(
            "POST", "/benefits", payload.model_dump(mode="json", exclude_none=True)
        )
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

    # --- Remit Control reads (no claimpayment / claimproc payment writes) ---

    def get_claimpayments(self) -> list[ODClaimPayment]:
        """GET /claimpayments — posted insurance checks / EFTs."""
        if self.replay_dir:
            payload = self._read_fixture("claimpayments")
        else:
            payload = self._get_json("/claimpayments")
        if not isinstance(payload, list):
            raise OpenDentalAPIError("OpenDental claimpayments payload was not a list")
        return [ODClaimPayment.model_validate(row) for row in payload]

    def get_deposits(self) -> list[ODDeposit]:
        """GET /deposits — deposit slips (optional recon input)."""
        try:
            if self.replay_dir:
                payload = self._read_fixture("deposits")
            else:
                payload = self._get_json("/deposits")
        except OpenDentalAPIError:
            return []
        if not isinstance(payload, list):
            return []
        return [ODDeposit.model_validate(row) for row in payload]

    def get_etrans_message_text(self, etrans_message_text_num: int) -> ODEtransMessageText:
        """GET /etransmessagetexts/{id} — raw ISA/ST*835 MessageText."""
        stem = f"etransmessagetext_{int(etrans_message_text_num)}"
        if self.replay_dir:
            payload = self._read_fixture(stem)
        else:
            payload = self._get_json(f"/etransmessagetexts/{int(etrans_message_text_num)}")
        if not isinstance(payload, dict):
            raise OpenDentalAPIError("OpenDental etransmessagetext payload was not an object")
        return ODEtransMessageText.model_validate(payload)

    def short_query(
        self, sql: str, *, offset: int = 0, replay_stem: str | None = None
    ) -> list[dict[str, object]]:
        """
        PUT /queries/ShortQuery — read-only SELECT helper (ApiQueries permission).

        Only SELECT statements are allowed. Used for clinic-wide ERA etrans discovery
        (GET /etranss requires PatNum and cannot poll practice-wide).
        ``replay_stem`` selects a fixture file when ``replay_dir`` is set; the
        default keeps the ERA etrans fixture used by remit ingest.
        """
        command = (sql or "").strip()
        if not command:
            raise OpenDentalAPIError("ShortQuery SQL is empty")
        lowered = f" {command.lstrip().lower()} "
        if not command.lstrip().lower().startswith("select"):
            raise OpenDentalAPIError("ShortQuery only permits SELECT statements")
        for bad in (" insert ", " update ", " delete ", " drop ", " alter ", " truncate "):
            if bad in lowered:
                raise OpenDentalAPIError("ShortQuery rejects mutating SQL")
        if ";" in command.rstrip().rstrip(";"):
            raise OpenDentalAPIError("ShortQuery rejects multi-statement SQL")

        body: dict[str, object] = {"SqlCommand": command, "Offset": int(offset)}
        if self.replay_dir:
            stem = replay_stem or (
                "shortquery_era_etrans" if offset == 0 else f"shortquery_era_etrans_{offset}"
            )
            payload = self._read_fixture(stem)
        else:
            payload = self._send_json("PUT", "/queries/ShortQuery", body)
        if isinstance(payload, dict) and isinstance(payload.get("Table"), list):
            rows = payload["Table"]
        elif isinstance(payload, list):
            rows = payload
        else:
            raise OpenDentalAPIError("OpenDental ShortQuery payload was not a row list")
        return [dict(row) for row in rows if isinstance(row, dict)]

    def list_era_835_etrans(
        self,
        *,
        after_datetime: str | None = None,
        after_etrans_num: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """
        Clinic-wide ERA_835 etrans rows newer than the ingest watermark.

        Open Dental stores ERA type as EtransType.ERA_835 (= 17 in classic builds).
        """
        clauses = ["Etype = 17"]
        if after_datetime:
            safe = after_datetime.replace("'", "")
            clauses.append(f"DateTimeTrans > '{safe}'")
        if after_etrans_num is not None:
            clauses.append(f"EtransNum > {int(after_etrans_num)}")
        where = " AND ".join(clauses)
        sql = (
            "SELECT EtransNum, EtransMessageTextNum, DateTimeTrans, Etype "
            f"FROM etrans WHERE {where} ORDER BY DateTimeTrans, EtransNum"
        )
        return self.short_query(sql, offset=offset)

    def get_procedures_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent procedurelog rows for one patient (fixed SELECT, int-coerced PatNum)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT pl.ProcNum, pl.PatNum, pl.AptNum, pl.ProcDate, pl.ProcStatus, "
            "pl.ProcFee, pl.ToothNum, pl.Surf, pc.ProcCode, pc.Descript "
            "FROM procedurelog pl "
            "LEFT JOIN procedurecode pc ON pc.CodeNum = pl.CodeNum "
            f"WHERE pl.PatNum = {safe_pat} "
            "ORDER BY pl.ProcDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_procedures_{safe_pat}")

    def get_claims_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent claim headers for one patient (fixed SELECT, int-coerced PatNum)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT ClaimNum, PatNum, DateService, ClaimStatus, ClaimFee, "
            "InsPayAmt, ClaimIdentifier "
            f"FROM claim WHERE PatNum = {safe_pat} "
            "ORDER BY DateService DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_claims_{safe_pat}")

    def get_appointments_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent appointments for one patient (status decoded, provider abbr)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT a.AptNum, a.PatNum, a.AptDateTime, "
            "CASE a.AptStatus "
            "WHEN 1 THEN 'Scheduled' WHEN 2 THEN 'Complete' WHEN 3 THEN 'UnschedList' "
            "WHEN 4 THEN 'ASAP' WHEN 5 THEN 'Broken' WHEN 6 THEN 'Planned' "
            "ELSE CAST(a.AptStatus AS CHAR) END AS AptStatus, "
            "a.Pattern, a.Confirmed, a.IsHygiene, a.Op, p.Abbr AS ProvAbbr, a.Note "
            "FROM appointment a "
            "LEFT JOIN provider p ON p.ProvNum = a.ProvNum "
            f"WHERE a.PatNum = {safe_pat} "
            "ORDER BY a.AptDateTime DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_appointments_{safe_pat}")

    def get_treatment_plan_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Treatment-planned procedurelog rows (ProcStatus = 1 / TP)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT pl.ProcNum, pl.PatNum, pl.ProcDate, pl.ProcFee, pl.ToothNum, "
            "pl.Surf, pc.ProcCode, pc.Descript "
            "FROM procedurelog pl "
            "LEFT JOIN procedurecode pc ON pc.CodeNum = pl.CodeNum "
            f"WHERE pl.PatNum = {safe_pat} AND pl.ProcStatus = 1 "
            "ORDER BY pl.ProcDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_treatmentplan_{safe_pat}")

    def get_account_summary_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Aging and estimated balance from the patient row."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT PatNum, BalTotal, Bal_0_30, Bal_31_60, Bal_61_90, BalOver90, "
            "InsEst, EstBalance, PayPlanDue "
            f"FROM patient WHERE PatNum = {safe_pat} LIMIT 1"
        )
        return self.short_query(sql, replay_stem=f"shortquery_account_{safe_pat}")

    def get_payments_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent payment rows for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT PayNum, PatNum, PayDate, PayAmt, PayType, CheckNum, PayNote "
            f"FROM payment WHERE PatNum = {safe_pat} "
            "ORDER BY PayDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_payments_{safe_pat}")

    def get_adjustments_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent adjustment rows for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT AdjNum, PatNum, AdjDate, AdjAmt, AdjType, ProcNum, ProvNum, AdjNote "
            f"FROM adjustment WHERE PatNum = {safe_pat} "
            "ORDER BY AdjDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_adjustments_{safe_pat}")

    def get_claim_procedures_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent claimproc rows with decoded Status."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT ClaimProcNum, ClaimNum, ProcNum, "
            "CASE Status "
            "WHEN 0 THEN 'NotReceived' WHEN 1 THEN 'Received' WHEN 2 THEN 'Preauth' "
            "WHEN 3 THEN 'Adjustment' WHEN 4 THEN 'Supplemental' WHEN 5 THEN 'CapClaim' "
            "WHEN 6 THEN 'Estimate' WHEN 7 THEN 'CapEstimate' WHEN 8 THEN 'CapComplete' "
            "WHEN 9 THEN 'InsHist' ELSE CAST(Status AS CHAR) END AS Status, "
            "InsPayEst, InsPayAmt, DedApplied, WriteOff, DateCP "
            f"FROM claimproc WHERE PatNum = {safe_pat} "
            "ORDER BY DateCP DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_claimprocs_{safe_pat}")

    def get_recalls_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recall rows for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT RecallNum, PatNum, DateDue, DateScheduled, DatePrevious, "
            "RecallStatus, RecallTypeNum, IsDisabled "
            f"FROM recall WHERE PatNum = {safe_pat} "
            "ORDER BY DateDue DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_recalls_{safe_pat}")

    def get_commlogs_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent communication-log notes for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT CommlogNum, PatNum, CommDateTime, CommType, Mode_, "
            "SentOrReceived, Note "
            f"FROM commlog WHERE PatNum = {safe_pat} "
            "ORDER BY CommDateTime DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_commlogs_{safe_pat}")

    def get_documents_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Document metadata only (no bytes) for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT DocNum, PatNum, DateCreated, Description, FileName, "
            "DocCategory, ImgType "
            f"FROM document WHERE PatNum = {safe_pat} "
            "ORDER BY DateCreated DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_documents_{safe_pat}")

    def get_referrals_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Referral attachments joined to the referral directory."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT ra.RefAttachNum, ra.PatNum, ra.RefType, ra.RefDate, "
            "r.LName, r.FName, r.Specialty, ra.Note "
            "FROM refattach ra "
            "LEFT JOIN referral r ON r.ReferralNum = ra.ReferralNum "
            f"WHERE ra.PatNum = {safe_pat} "
            "ORDER BY ra.RefDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_referrals_{safe_pat}")

    def get_statements_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent billing statements for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT StatementNum, PatNum, DateSent, Mode_, IsSent, DocNum, Note "
            f"FROM statement WHERE PatNum = {safe_pat} "
            "ORDER BY DateSent DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_statements_{safe_pat}")

    def get_medications_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Medication history (medicationpat + catalog name)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT mp.MedicationPatNum, mp.PatNum, "
            "COALESCE(m.MedName, mp.MedDescript) AS MedName, "
            "mp.PatNote, mp.DateStart, mp.DateStop "
            "FROM medicationpat mp "
            "LEFT JOIN medication m ON m.MedicationNum = mp.MedicationNum "
            f"WHERE mp.PatNum = {safe_pat} "
            "ORDER BY mp.DateStart DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_medications_{safe_pat}")

    def get_allergies_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Allergy rows joined to allergydef."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT a.AllergyNum, a.PatNum, d.Description, a.Reaction, "
            "a.DateAdverseReaction, a.StatusIsActive "
            "FROM allergy a "
            "LEFT JOIN allergydef d ON d.AllergyDefNum = a.AllergyDefNum "
            f"WHERE a.PatNum = {safe_pat} "
            "ORDER BY a.DateAdverseReaction DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_allergies_{safe_pat}")

    def get_problems_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Problem / disease list with decoded ProbStatus."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT ds.DiseaseNum, ds.PatNum, dd.DiseaseName, "
            "CASE ds.ProbStatus WHEN 0 THEN 'Active' WHEN 1 THEN 'Resolved' "
            "WHEN 2 THEN 'Inactive' ELSE CAST(ds.ProbStatus AS CHAR) END AS ProbStatus, "
            "ds.DateStart, ds.DateStop, ds.PatNote "
            "FROM disease ds "
            "LEFT JOIN diseasedef dd ON dd.DiseaseDefNum = ds.DiseaseDefNum "
            f"WHERE ds.PatNum = {safe_pat} "
            "ORDER BY ds.DateStart DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_problems_{safe_pat}")

    def get_perio_exams_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Perio exam headers only (no measures, to bound payload)."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT PerioExamNum, PatNum, ExamDate, ProvNum "
            f"FROM perioexam WHERE PatNum = {safe_pat} "
            "ORDER BY ExamDate DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_perioexams_{safe_pat}")

    def get_clinical_notes_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Recent procedure notes (procnote) for one patient."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT ProcNoteNum, PatNum, ProcNum, EntryDateTime, UserNum, Note "
            f"FROM procnote WHERE PatNum = {safe_pat} "
            "ORDER BY EntryDateTime DESC LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_procnotes_{safe_pat}")

    def get_family_members_for_patient(self, pat_num: int) -> list[dict[str, object]]:
        """Patient rows that share this patient's Guarantor."""
        safe_pat = int(pat_num)
        sql = (
            "SELECT p.PatNum, p.FName, p.LName, p.Position, p.Birthdate, p.Guarantor "
            "FROM patient p "
            "WHERE p.Guarantor = ("
            f"SELECT Guarantor FROM patient WHERE PatNum = {safe_pat}"
            ") ORDER BY p.PatNum LIMIT 25"
        )
        return self.short_query(sql, replay_stem=f"shortquery_family_{safe_pat}")
