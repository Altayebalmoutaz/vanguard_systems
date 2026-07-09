from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.integrations.opendental import post_check as mod


def _row(*, writeback_enabled: bool = True, writeback_full: bool = False) -> dict:
    return {
        "patient_id": str(uuid4()),
        "input_json": {
            "source": "opendental",
            "pat_num": 42,
            "writeback_enabled": writeback_enabled,
            "writeback_full": writeback_full,
            "primary_pat_plan_num": 1,
            "primary_plan_num": 2,
            "primary_ins_sub_num": 3,
            "primary_carrier_name": "Delta",
        },
    }


def _result() -> dict:
    return {
        "primary": {
            "check_id": "check-1",
            "routing": {"status": "CLEARED"},
            "canonical": {"is_active": True},
        }
    }


def test_maybe_enqueue_skips_non_opendental_source(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True),
    )
    row = _row()
    row["input_json"]["source"] = "dashboard"
    assert mod.maybe_enqueue_od_writeback(
        SimpleNamespace(),
        practice_id="clinic_a",
        request_id=uuid4(),
        row=row,
        result=_result(),
    ) is None


def test_maybe_enqueue_full_writeback_sets_all_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True),
    )
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: {"writeback_enabled": True})
    captured: dict = {}

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"pat_num": 42}

    monkeypatch.setattr(mod, "build_opendental_writeback_payload", fake_build)
    monkeypatch.setattr(mod, "enqueue_opendental_writeback", lambda *a, **k: uuid4())

    out = mod.maybe_enqueue_od_writeback(
        SimpleNamespace(),
        practice_id="clinic_a",
        request_id=uuid4(),
        row=_row(writeback_full=True),
        result=_result(),
    )

    assert out is not None
    assert captured["write_benefit_notes"] is True
    assert captured["write_commlog"] is True
    assert captured["write_insadjust"] is True
    assert captured["write_benefits_grid"] is True


def test_maybe_enqueue_partial_writeback_omits_grid(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True),
    )
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: {"writeback_enabled": True})
    captured: dict = {}

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"pat_num": 42}

    monkeypatch.setattr(mod, "build_opendental_writeback_payload", fake_build)
    monkeypatch.setattr(mod, "enqueue_opendental_writeback", lambda *a, **k: uuid4())

    mod.maybe_enqueue_od_writeback(
        SimpleNamespace(),
        practice_id="clinic_a",
        request_id=uuid4(),
        row=_row(writeback_full=False),
        result=_result(),
    )

    assert captured["write_insadjust"] is False
    assert captured["write_benefits_grid"] is False
