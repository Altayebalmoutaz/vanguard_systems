from __future__ import annotations

from app.integrations.opendental.models import (
    ODBenefit,
    ODCommlogResponse,
    ODCovCat,
    ODInsVerifyResponse,
)
from app.integrations.opendental.writeback import (
    build_benefit_grid_targets,
    build_benefit_snapshot,
    build_benefits_note,
    build_commlog_summary,
    build_enrollment_note,
    format_benefit_notes,
    run_opendental_benefits_grid_writeback,
    run_opendental_writeback,
)


def test_enrollment_note_includes_routing_and_procedures() -> None:
    note = build_enrollment_note(
        check_id="abc-123",
        routing={"status": "CLEARED", "action": "route_coding"},
        canonical={"is_active": True, "is_covered": True, "payer_id": "84103"},
        procedure_estimates=[
            {
                "cdt_code": "D1110",
                "procedure_covered": True,
                "patient_responsibility": 50.0,
                "insurance_pays": 70.0,
                "allowed_amount": 120.0,
            }
        ],
    )
    assert "CLEARED" in note
    assert "route_coding" in note
    assert "D1110" in note
    assert "$50.00" in note
    assert "abc-123" in note


def test_benefits_note_includes_financial_snapshot() -> None:
    note = build_benefits_note(
        check_id="abc-123",
        routing={"status": "CLEARED"},
        canonical={
            "is_active": True,
            "response_complete": True,
            "coverage_percent": 100,
            "coinsurance": 0.0,
            "deductible_remaining": 50,
            "annual_max_remaining": 1356,
            "annual_max_total": 1500,
        },
        procedure_estimates=[
            {"cdt_code": "D1110", "patient_responsibility": 50, "insurance_pays": 70}
        ],
    )
    assert "Vanguard MD - benefits snapshot" in note
    assert "Deductible remaining" in note
    assert "$1356.00" in note
    assert "D1110" in note


_CANONICAL = {
    "is_active": True,
    "response_complete": True,
    "coverage_percent": 100,
    "coinsurance": 0.0,
    "copay": 0,
    "deductible_total": 100,
    "deductible_remaining": 50,
    "annual_max_total": 1500,
    "annual_max_remaining": 1356,
}
_ESTIMATES = [
    {
        "cdt_code": "D1110",
        "patient_responsibility": 50,
        "insurance_pays": 70,
        "allowed_amount": 120,
    },
    {
        "cdt_code": "D2740",
        "patient_responsibility": 400,
        "insurance_pays": 400,
        "allowed_amount": 800,
    },
]


def test_format_benefit_notes_is_deterministic_ascii() -> None:
    snapshot = build_benefit_snapshot(
        routing={"status": "CLEARED"},
        canonical=_CANONICAL,
        procedure_estimates=_ESTIMATES,
        carrier_name="Aetna",
        plan_name="PPO",
        check_id="abc-123",
        now=__import__("datetime").datetime(2026, 5, 29, 10, 49),
    )
    note = format_benefit_notes(snapshot)
    assert note.startswith("[ELIGIBILITY SNAPSHOT | STEDI]")
    assert "Date: 2026-05-29 10:49" in note
    assert "Plan: PPO - Aetna" in note
    assert "Total: $100.00" in note
    assert "Remaining: $1356.00" in note
    assert "D1110: 58%" in note  # 70/120
    assert "D2740: 50%" in note  # 400/800
    assert "Source: Stedi" in note
    assert "Agent: eligibility-agent-v1" in note
    # ASCII only (no encoding artifacts in OD/PowerShell).
    note.encode("ascii")


def test_format_benefit_notes_renders_na_for_missing_fields() -> None:
    snapshot = build_benefit_snapshot(
        routing={"status": "ACTION_REQUIRED"},
        canonical={"is_active": True},
        procedure_estimates=[],
    )
    note = format_benefit_notes(snapshot)
    assert "Plan: n/a" in note
    assert "Coverage:\n - n/a" in note
    assert "Frequency:\n - n/a" in note


def test_commlog_summary_is_concise_ascii() -> None:
    snapshot = build_benefit_snapshot(
        routing={"status": "CLEARED"},
        canonical=_CANONICAL,
        procedure_estimates=_ESTIMATES,
        carrier_name="Aetna",
    )
    summary = build_commlog_summary(snapshot)
    assert summary.startswith("[Eligibility - Stedi]")
    assert "CLEARED" in summary
    summary.encode("ascii")


class _WBStub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.insverifies: list[str] = []

    def update_inssub_benefit_notes(self, ins_sub_num, plan_num, benefit_notes):  # type: ignore[no-untyped-def]
        self.calls.append("benefit_notes")
        return {"InsSubNum": ins_sub_num}

    def update_inssub_subscriber_note(self, ins_sub_num, plan_num, subscriber_note):  # type: ignore[no-untyped-def]
        self.calls.append("subscriber_note")
        return {"InsSubNum": ins_sub_num}

    def create_insverify(self, payload):  # type: ignore[no-untyped-def]
        self.calls.append("insverify")
        self.insverifies.append(payload.VerifyType)
        return ODInsVerifyResponse(InsVerifyNum=1, VerifyType=payload.VerifyType, FKey=payload.FKey)

    def create_commlog(self, pat_num, note, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append("commlog")
        return ODCommlogResponse(CommlogNum=9, PatNum=pat_num, Note=note)


def test_run_writeback_order_and_isolation() -> None:
    stub = _WBStub()
    result = run_opendental_writeback(
        stub,  # type: ignore[arg-type]
        pat_num=24,
        primary_pat_plan_num=101,
        primary_plan_num=301,
        primary_ins_sub_num=201,
        primary_result={
            "check_id": "c1",
            "routing": {"status": "CLEARED"},
            "canonical": _CANONICAL,
            "procedure_estimates": _ESTIMATES,
        },
        carrier_name="Aetna",
    )
    # Order: BenefitNotes, SubscNote, the two InsVerifies, then Commlog.
    assert stub.calls == ["benefit_notes", "subscriber_note", "insverify", "insverify", "commlog"]
    assert set(stub.insverifies) == {"PatientEnrollment", "InsuranceBenefit"}
    assert result["benefit_notes"]["ins_sub_num"] == 201
    assert result["subscriber_note"]["ins_sub_num"] == 201
    assert "Eligibility CLEARED" in result["subscriber_note"]["note_sent"]
    assert result["commlog"]["pat_num"] == 24
    assert result["write_back_result"]["InsVerifyNum"] == 1
    assert result["insadjust"] is None  # default off


def test_run_writeback_isolates_benefit_notes_failure() -> None:
    class _Boom(_WBStub):
        def update_inssub_benefit_notes(self, *a, **k):  # type: ignore[no-untyped-def]
            self.calls.append("benefit_notes")
            raise RuntimeError("inssubs down")

    stub = _Boom()
    result = run_opendental_writeback(
        stub,  # type: ignore[arg-type]
        pat_num=24,
        primary_pat_plan_num=101,
        primary_plan_num=301,
        primary_ins_sub_num=201,
        primary_result={
            "routing": {"status": "CLEARED"},
            "canonical": _CANONICAL,
            "procedure_estimates": [],
        },
    )
    # BenefitNotes failed but SubscNote + InsVerifies + Commlog still ran.
    assert "error" in result["benefit_notes"]
    assert stub.calls == ["benefit_notes", "subscriber_note", "insverify", "insverify", "commlog"]
    assert result["write_back_result"]["InsVerifyNum"] == 1


_COVCATS = [
    ODCovCat(CovCatNum=1, Description="General", EbenefitCat="General"),
    ODCovCat(CovCatNum=2, Description="Diagnostic", EbenefitCat="Diagnostic"),
    ODCovCat(CovCatNum=4, Description="Restorative", EbenefitCat="Restorative"),
    ODCovCat(CovCatNum=8, Description="Crowns", EbenefitCat="Crowns"),
    ODCovCat(CovCatNum=12, Description="Ortho", EbenefitCat="Orthodontics"),
]
_UNIVERSAL_RECORD = {
    "categories": [
        {
            "category": "DIAGNOSTIC",
            "covered": {"value": True},
            "coinsurance_patient_pct": {"value": 0.0},
        },
        {
            "category": "BASIC",
            "covered": {"value": True},
            "coinsurance_patient_pct": {"value": 20.0},
        },
        {
            "category": "MAJOR",
            "covered": {"value": True},
            "coinsurance_patient_pct": {"value": 50.0},
        },
    ]
}


def test_build_benefit_grid_targets_maps_coverage_and_totals() -> None:
    targets = build_benefit_grid_targets(canonical=_CANONICAL, universal_record=_UNIVERSAL_RECORD)
    by_label = {t["label"]: t["percent"] for t in targets["coverage"]}
    assert by_label["DIAGNOSTIC"] == 100
    assert by_label["BASIC"] == 80
    assert by_label["MAJOR"] == 50
    assert targets["annual_max"] == 1500
    assert targets["deductible"] == 100


class _BenefitsStub:
    def __init__(self, existing: list[ODBenefit]) -> None:
        self.existing = existing
        self.created: list[dict] = []
        self.updated: list[tuple[int, dict]] = []
        self._next = 900

    def get_covcats(self):  # type: ignore[no-untyped-def]
        return _COVCATS

    def get_benefits(self, plan_num):  # type: ignore[no-untyped-def]
        return self.existing

    def create_benefit(self, payload):  # type: ignore[no-untyped-def]
        self._next += 1
        self.created.append(payload.model_dump(exclude_none=True))
        return ODBenefit(BenefitNum=self._next, **payload.model_dump(exclude_none=True))

    def update_benefit(self, benefit_num, payload):  # type: ignore[no-untyped-def]
        self.updated.append((benefit_num, payload.model_dump(exclude_none=True)))
        return ODBenefit(BenefitNum=benefit_num, **payload.model_dump(exclude_none=True))


def test_benefits_grid_upsert_creates_and_updates() -> None:
    # Diagnostic already 100 (unchanged); Restorative at 50 (update -> 80); annual max missing (create).
    existing = [
        ODBenefit(BenefitNum=189, PlanNum=19, CovCatNum=2, BenefitType="CoInsurance", Percent=100),
        ODBenefit(BenefitNum=192, PlanNum=19, CovCatNum=4, BenefitType="CoInsurance", Percent=50),
    ]
    stub = _BenefitsStub(existing)
    result = run_opendental_benefits_grid_writeback(
        stub,  # type: ignore[arg-type]
        plan_num=19,
        canonical=_CANONICAL,
        universal_record=_UNIVERSAL_RECORD,
    )
    actions = {(a["target"], a["action"]) for a in result["actions"]}
    assert ("DIAGNOSTIC/Diagnostic", "unchanged") in actions
    assert ("BASIC/Restorative", "updated") in actions
    assert ("MAJOR/Crowns", "created") in actions
    # Annual max + general deductible created against the General covcat (1).
    assert ("annual_max", "created") in actions
    assert any(c.get("MonetaryAmt") == 1500 and c.get("CovCatNum") == 1 for c in stub.created)
    assert (192, {"Percent": 80}) in stub.updated


def test_benefits_grid_isolates_row_failure() -> None:
    class _Boom(_BenefitsStub):
        def update_benefit(self, benefit_num, payload):  # type: ignore[no-untyped-def]
            raise RuntimeError("benefits put down")

    existing = [
        ODBenefit(BenefitNum=192, PlanNum=19, CovCatNum=4, BenefitType="CoInsurance", Percent=50)
    ]
    stub = _Boom(existing)
    result = run_opendental_benefits_grid_writeback(
        stub,  # type: ignore[arg-type]
        plan_num=19,
        canonical=_CANONICAL,
        universal_record=_UNIVERSAL_RECORD,
    )
    # The failing Restorative update is captured as an error but others still created.
    assert any("error" in a for a in result["actions"])
    assert any(a.get("action") == "created" for a in result["actions"])
