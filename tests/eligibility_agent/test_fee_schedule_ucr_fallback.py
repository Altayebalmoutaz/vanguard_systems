"""UCR fallback merge for Layer 5 fee schedules."""

from types import SimpleNamespace

from app.eligibility.fee_schedule import merge_ucr_fallback_into_fee_schedule


def _settings(*, enabled: bool = True, json_override: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        eligibility_ucr_fallback_enabled=enabled,
        eligibility_ucr_fallback_json=json_override,
    )


def test_merge_ucr_fallback_fills_zero_billed() -> None:
    fee: dict = {"contracted": {"84103": {}}, "billed": {}}
    merge_ucr_fallback_into_fee_schedule(fee, "84103", ["D1110", "D2740"], _settings())

    assert fee["billed"]["D1110"] == 165.0
    assert fee["billed"]["D2740"] == 1100.0
    assert fee["contracted"]["84103"]["D1110"] == 165.0
    assert fee["contracted"]["84103"]["D2740"] == 1100.0


def test_merge_ucr_fallback_skips_when_disabled() -> None:
    fee: dict = {"contracted": {"P": {}}, "billed": {}}
    merge_ucr_fallback_into_fee_schedule(fee, "P", ["D1110"], _settings(enabled=False))
    assert fee["billed"] == {}


def test_merge_ucr_fallback_json_overrides_builtin() -> None:
    fee: dict = {"contracted": {"X": {}}, "billed": {}}
    merge_ucr_fallback_into_fee_schedule(
        fee, "X", ["D1110"], _settings(json_override='{"D1110": 199.5}')
    )
    assert fee["billed"]["D1110"] == 199.5


def test_merge_ucr_fallback_does_not_override_positive_db_fee() -> None:
    fee: dict = {"contracted": {"84103": {"D1110": 200.0}}, "billed": {"D1110": 200.0}}
    merge_ucr_fallback_into_fee_schedule(fee, "84103", ["D1110"], _settings())
    assert fee["billed"]["D1110"] == 200.0
    assert fee["contracted"]["84103"]["D1110"] == 200.0
