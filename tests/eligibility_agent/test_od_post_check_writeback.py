from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.integrations.opendental import post_check as mod


def _row(
    *,
    writeback_enabled: bool = True,
    writeback_full: bool = False,
    writeback_shadow_compare: bool = False,
    with_secondary: bool = False,
) -> dict:
    input_json: dict = {
        "source": "opendental",
        "pat_num": 42,
        "writeback_enabled": writeback_enabled,
        "writeback_full": writeback_full,
        "writeback_shadow_compare": writeback_shadow_compare,
        "primary_pat_plan_num": 1,
        "primary_plan_num": 2,
        "primary_ins_sub_num": 3,
        "primary_carrier_name": "Delta",
    }
    if with_secondary:
        input_json.update(
            {
                "secondary_pat_plan_num": 11,
                "secondary_plan_num": 12,
                "secondary_ins_sub_num": 13,
                "secondary_carrier_name": "MetLife",
            }
        )
    return {"patient_id": str(uuid4()), "input_json": input_json}


def _result(*, with_secondary: bool = False) -> dict:
    out = {
        "primary": {
            "check_id": "check-1",
            "routing": {"status": "CLEARED"},
            "canonical": {"is_active": True},
        }
    }
    if with_secondary:
        out["secondary"] = {
            "check_id": "check-2",
            "routing": {"status": "CLEARED"},
            "canonical": {"is_active": True},
        }
    return out


def test_maybe_enqueue_skips_non_opendental_source(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True
        ),
    )
    row = _row()
    row["input_json"]["source"] = "dashboard"
    assert (
        mod.maybe_enqueue_od_writeback(
            SimpleNamespace(),
            practice_id="clinic_a",
            request_id=uuid4(),
            row=row,
            result=_result(),
        )
        is None
    )


def test_maybe_enqueue_full_writeback_sets_all_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_connection",
        lambda *a, **k: {"writeback_enabled": True, "writeback_full": True},
    )
    captured: list[dict] = []

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.append(dict(kwargs))
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
    assert out["queued"] is True
    assert len(captured) == 1
    assert captured[0]["write_benefit_notes"] is True
    assert captured[0]["write_commlog"] is True
    assert captured[0]["write_insadjust"] is True
    assert captured[0]["write_benefits_grid"] is True
    assert captured[0]["write_inshist"] is True
    assert captured[0]["dry_run_financial"] is False


def test_maybe_enqueue_shadow_compare_sets_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_connection",
        lambda *a, **k: {
            "writeback_enabled": True,
            "writeback_full": True,
            "writeback_shadow_compare": True,
        },
    )
    captured: list[dict] = []

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.append(dict(kwargs))
        return {"pat_num": 42}

    monkeypatch.setattr(mod, "build_opendental_writeback_payload", fake_build)
    monkeypatch.setattr(mod, "enqueue_opendental_writeback", lambda *a, **k: uuid4())

    mod.maybe_enqueue_od_writeback(
        SimpleNamespace(),
        practice_id="clinic_a",
        request_id=uuid4(),
        row=_row(writeback_full=True, writeback_shadow_compare=True),
        result=_result(),
    )

    assert captured[0]["dry_run_financial"] is True
    assert captured[0]["write_benefits_grid"] is True


def test_maybe_enqueue_secondary_when_full(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_connection",
        lambda *a, **k: {"writeback_enabled": True, "writeback_full": True},
    )
    captured: list[dict] = []

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.append(dict(kwargs))
        return {"pat_num": 42}

    monkeypatch.setattr(mod, "build_opendental_writeback_payload", fake_build)
    monkeypatch.setattr(mod, "enqueue_opendental_writeback", lambda *a, **k: uuid4())

    out = mod.maybe_enqueue_od_writeback(
        SimpleNamespace(),
        practice_id="clinic_a",
        request_id=uuid4(),
        row=_row(writeback_full=True, with_secondary=True),
        result=_result(with_secondary=True),
    )

    assert out is not None
    assert len(out["runs"]) == 2
    orders = {c["coverage_order"] for c in captured}
    assert orders == {"primary", "secondary"}
    secondary = next(c for c in captured if c["coverage_order"] == "secondary")
    assert secondary["primary_pat_plan_num"] == 11
    assert secondary["primary_plan_num"] == 12


def test_maybe_enqueue_partial_writeback_omits_grid(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            pilot_shadow_mode=False, opendental_write_benefits_grid_respect_manual_edits=True
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_connection",
        lambda *a, **k: {"writeback_enabled": True, "writeback_full": False},
    )
    captured: list[dict] = []

    def fake_build(**kwargs):  # type: ignore[no-untyped-def]
        captured.append(dict(kwargs))
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

    assert captured[0]["write_insadjust"] is False
    assert captured[0]["write_benefits_grid"] is False
    assert captured[0]["write_inshist"] is False
