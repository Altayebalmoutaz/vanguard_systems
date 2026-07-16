from __future__ import annotations

from app.integrations.opendental.cdt_resolve import (
    OD_TRIAL_CODE_ALIASES,
    resolve_appointment_procedures,
    resolve_od_proc_code,
)


def test_ada_pass_through() -> None:
    hit = resolve_od_proc_code("d1110")
    assert hit is not None
    assert hit.cdt_code == "D1110"
    assert hit.source == "ada"


def test_trial_t_aliases() -> None:
    assert resolve_od_proc_code("T3541").cdt_code == "D1110"  # type: ignore[union-attr]
    assert resolve_od_proc_code("T1665").cdt_code == "D0330"  # type: ignore[union-attr]
    assert resolve_od_proc_code("T1698").cdt_code == "D0274"  # type: ignore[union-attr]
    assert resolve_od_proc_code("T1632").cdt_code == "D0272"  # type: ignore[union-attr]
    assert resolve_od_proc_code("T1356").cdt_code == "D0120"  # type: ignore[union-attr]
    assert resolve_od_proc_code("T6531").cdt_code == "D2750"  # type: ignore[union-attr]
    assert OD_TRIAL_CODE_ALIASES["T3541"] == "D1110"


def test_unknown_t_returns_none() -> None:
    assert resolve_od_proc_code("T9999") is None
    assert resolve_od_proc_code("") is None
    assert resolve_od_proc_code(None) is None


def test_resolve_appointment_falls_back_to_clinic_defaults() -> None:
    result = resolve_appointment_procedures(
        [{"procCode": "T9999", "descript": "Mystery"}],
        clinic_defaults=["D1110", "D0120"],
    )
    assert result.cdt_source == "clinic_default"
    assert result.cdt_codes == ["D1110", "D0120"]
    assert len(result.unmapped) == 1
    assert result.unmapped[0].od_proc_code == "T9999"


def test_resolve_appointment_merges_and_dedupes() -> None:
    result = resolve_appointment_procedures(
        [
            {"procCode": "T3541", "descript": "Prophy, Adult"},
            {"procCode": "T1665", "descript": "Panoramic"},
            {"procCode": "D1110", "descript": "prophylaxis"},
            {"procCode": "T9999", "descript": "Unknown"},
        ],
        clinic_defaults=["D2740"],
    )
    assert result.cdt_source == "appointment"
    assert result.cdt_codes == ["D1110", "D0330"]
    assert len(result.unmapped) == 1
    provenance = result.to_input_json(apt_nums=[10, 11])
    assert provenance["apt_nums"] == [10, 11]
    assert provenance["cdt_source"] == "appointment"
    assert provenance["unmapped_od_codes"][0]["od_proc_code"] == "T9999"


def test_empty_rows_use_clinic_defaults() -> None:
    result = resolve_appointment_procedures([], clinic_defaults=["D1110"])
    assert result.cdt_source == "clinic_default"
    assert result.cdt_codes == ["D1110"]
