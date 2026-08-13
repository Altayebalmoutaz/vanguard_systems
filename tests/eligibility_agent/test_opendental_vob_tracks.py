"""Tests for OD VOB writeback tracks A–G (targets, diff, dry-run, fees, drift, reverify)."""

from __future__ import annotations

from app.integrations.opendental.benefit_diff import (
    classify_coinsurance_change,
    classify_monetary_change,
    classify_quantity_change,
    summarize_dispositions,
)
from app.integrations.opendental.fee_schedule_intel import detect_fee_schedule_alerts
from app.integrations.opendental.insplan_drift import detect_insplan_drift
from app.integrations.opendental.models import ODBenefit, ODCovCat
from app.integrations.opendental.reverify import (
    effective_poll_window_days,
    material_change_alert_items,
)
from app.integrations.opendental.writeback import (
    build_benefit_grid_targets,
    run_opendental_benefits_grid_writeback,
)


def test_benefit_diff_classifiers() -> None:
    assert classify_coinsurance_change(previous=None, proposed=80).disposition == "auto"
    assert classify_coinsurance_change(previous=80, proposed=80).disposition == "unchanged"
    assert classify_coinsurance_change(previous=80, proposed=84).disposition == "auto"
    assert classify_coinsurance_change(previous=80, proposed=50).disposition == "review"
    assert classify_monetary_change(previous=1000, proposed=1050, field="x").disposition == "auto"
    assert classify_monetary_change(previous=1000, proposed=1500, field="x").disposition == "review"
    assert classify_quantity_change(previous=2, proposed=3, field="q").disposition == "review"


def test_build_targets_active_ortho_copay_and_fine_categories() -> None:
    canonical = {
        "is_active": True,
        "annual_max_total": 1500,
        "deductible_total": 50,
        "copay": 25,
        "dental_benefit_breakdown": {
            "ortho_lifetime_max": 2000,
            "frequency_limitations": [],
            "waiting_periods": [],
            "missing_tooth_clause": {"present": False},
            "limitation_notes": ["Composite downgraded to amalgam"],
        },
    }
    udr = {
        "categories": [
            {"category": "ENDO", "coinsurance_patient_pct": {"value": 20.0}},
            {"category": "PERIO", "coinsurance_patient_pct": {"value": 20.0}},
            {"category": "ORTHO", "coinsurance_patient_pct": {"value": 50.0}},
        ],
        "financial": {"ortho_lifetime_max": {"value": 2000}},
        "frequency_limitations": [
            {
                "category": "PREVENTIVE",
                "quantity": 2,
                "quantity_qualifier": "NumberOfServices",
                "period_months": 12,
                "description": "cleanings",
            }
        ],
    }
    targets = build_benefit_grid_targets(canonical=canonical, universal_record=udr)
    assert targets["active_coverage"] is True
    assert targets["copay"] == 25.0
    assert targets["ortho_lifetime_max"] == 2000.0
    labels = {c["label"] for c in targets["coverage"]}
    assert "ENDO" in labels and "PERIO" in labels and "ORTHO" in labels
    assert targets["frequency_limitations"]
    assert any("downgraded" in c.lower() for c in targets["plan_clauses"])


def test_grid_dry_run_does_not_mutate() -> None:
    class _Stub:
        created = 0
        updated = 0

        def get_covcats(self):
            return [
                ODCovCat(CovCatNum=1, EbenefitCat="General"),
                ODCovCat(CovCatNum=4, EbenefitCat="Restorative"),
                ODCovCat(CovCatNum=12, EbenefitCat="Orthodontics"),
            ]

        def get_benefits(self, plan_num: int):
            return [
                ODBenefit(
                    BenefitNum=9,
                    PlanNum=plan_num,
                    BenefitType="CoInsurance",
                    CovCatNum=4,
                    Percent=50,
                )
            ]

        def create_benefit(self, payload):
            self.created += 1
            raise AssertionError("create_benefit should not run in dry_run")

        def update_benefit(self, benefit_num, payload):
            self.updated += 1
            raise AssertionError("update_benefit should not run in dry_run")

    stub = _Stub()
    result = run_opendental_benefits_grid_writeback(
        stub,  # type: ignore[arg-type]
        plan_num=7,
        canonical={
            "is_active": True,
            "annual_max_total": 1500,
            "deductible_total": 50,
            "copay": 10,
        },
        universal_record={
            "categories": [
                {"category": "BASIC", "coinsurance_patient_pct": {"value": 20.0}},
            ],
            "financial": {"ortho_lifetime_max": {"value": 1000}},
        },
        respect_manual_edits=False,
        dry_run=True,
        confidence_gating=True,
    )
    assert result["dry_run"] is True
    assert stub.created == 0 and stub.updated == 0
    actions = {a.get("action") for a in result["actions"]}
    assert "proposed_update" in actions or "proposed_create" in actions
    assert result["disposition_summary"]["auto"] >= 1


def test_confidence_gating_skips_large_coinsurance_delta() -> None:
    class _Stub:
        def get_covcats(self):
            return [
                ODCovCat(CovCatNum=1, EbenefitCat="General"),
                ODCovCat(CovCatNum=4, EbenefitCat="Restorative"),
            ]

        def get_benefits(self, plan_num: int):
            return [
                ODBenefit(
                    BenefitNum=9,
                    PlanNum=plan_num,
                    BenefitType="CoInsurance",
                    CovCatNum=4,
                    Percent=100,
                )
            ]

        def create_benefit(self, payload):
            raise AssertionError("should not create")

        def update_benefit(self, benefit_num, payload):
            raise AssertionError("should not update large delta under gating")

    result = run_opendental_benefits_grid_writeback(
        _Stub(),  # type: ignore[arg-type]
        plan_num=7,
        canonical={"is_active": True},
        universal_record={
            "categories": [
                {"category": "BASIC", "coinsurance_patient_pct": {"value": 50.0}},  # → 50%
            ]
        },
        # Agent-owned row may be updated, but large delta still needs review.
        respect_manual_edits=True,
        dry_run=False,
        confidence_gating=True,
        agent_benefit_nums={9},
    )
    assert any(a.get("action") == "skipped_needs_review" for a in result["actions"])
    assert result["review_items"]


def test_fee_schedule_and_insplan_drift() -> None:
    alerts = detect_fee_schedule_alerts(
        canonical={"in_network": False},
        universal_record={"network_status": "out_of_network"},
    )
    assert any(a["code"] == "out_of_network" for a in alerts)

    drift = detect_insplan_drift(
        od_snapshot={"group_number": "A1", "subscriber_id": "S1", "elect_id": "111"},
        canonical={"payer_id": "222", "is_active": True},
        universal_record={"group_number": "B2", "subscriber_id": "S1"},
    )
    fields = {d["field"] for d in drift}
    assert "group_number" in fields
    assert "payer_id" in fields


def test_reverify_window_and_change_alerts() -> None:
    assert (
        effective_poll_window_days(
            {"poll_window_days": 0, "poll_enabled": True}, default_reverify_days=3
        )
        == 3
    )
    assert (
        effective_poll_window_days(
            {"poll_window_days": 5, "poll_enabled": True}, default_reverify_days=3
        )
        == 5
    )
    assert (
        effective_poll_window_days(
            {"poll_window_days": 0, "poll_enabled": False}, default_reverify_days=3
        )
        == 0
    )
    assert (
        effective_poll_window_days(
            {"poll_window_days": 0, "poll_enabled": True},
            default_reverify_days=3,
            expand_when_zero=False,
        )
        == 0
    )
    items = material_change_alert_items(
        benefits_grid={
            "actions": [{"action": "proposed_update", "target": "BASIC", "type": "CoInsurance"}]
        },
        insadjust={"mode": "proposed", "ins_used": 100},
        insplan_drift=[{"field": "group_number", "disposition": "review"}],
    )
    assert len(items) >= 3
    assert summarize_dispositions([{"action": "skipped_needs_review"}])["review"] == 1
