from __future__ import annotations

from pathlib import Path

from app.copilot.patients import (
    copilot_patient_uuid,
    list_copilot_directory,
    list_opendental_directory,
    safe_search_fragment,
)
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.errors import OpenDentalAPIError

_FIXTURES = Path(__file__).parent / "fixtures" / "opendental"


def _client() -> OpenDentalClient:
    return OpenDentalClient(
        base_url="http://localhost:30222/api/v1",
        developer_key="dev",
        customer_key="cust",
        timeout_seconds=5.0,
        replay_dir=str(_FIXTURES),
    )


def test_safe_search_strips_sql_metacharacters() -> None:
    assert "'" not in safe_search_fragment("O'Brien; drop table")
    assert safe_search_fragment("%_") == ""


def test_list_opendental_directory_replay() -> None:
    rows = list_opendental_directory(_client())
    assert rows[0]["LName"] == "Dent"
    assert rows[1]["PatNum"] == 8


def test_merge_prefers_eligibility_uuid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    practice_id = "practice-1"
    elig_id = "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(
        "app.copilot.patients.get_connection",
        lambda *args, **kwargs: {"practice_id": practice_id},
    )
    monkeypatch.setattr(
        "app.copilot.patients.OpenDentalClient.from_connection",
        lambda *args, **kwargs: _client(),
    )
    monkeypatch.setattr(
        "app.copilot.patients.list_eligibility_queue",
        lambda *args, **kwargs: [
            {
                "patient_id": elig_id,
                "patient_name": "Aardvark Dent",
                "payer_label": "Aetna",
                "od_pat_num": "1",
            }
        ],
    )

    result = list_copilot_directory(
        object(),  # type: ignore[arg-type]
        practice_id=practice_id,
    )
    by_name = {item["name"]: item for item in result["patients"]}
    aardvark = by_name["Aardvark Dent"]
    assert aardvark["patient_id"] == elig_id
    assert aardvark["od_pat_num"] == 1
    assert aardvark["sources"] == ["opendental", "eligibility"]
    mira = by_name["Mira Chen"]
    assert mira["patient_id"] == str(copilot_patient_uuid(practice_id, 8))
    assert mira["sources"] == ["opendental"]
    assert result["opendental_connected"] is True
    assert result["opendental_error"] is None


def test_directory_surfaces_econnector_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _fail(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise OpenDentalAPIError(
            "OpenDental PUT failed for /queries/ShortQuery",
            status_code=400,
            body='"The office\'s eConnector is not running. Please contact the office to start their eConnector."',
        )

    monkeypatch.setattr(
        "app.copilot.patients.get_connection",
        lambda *args, **kwargs: {"practice_id": "partner_clinic"},
    )
    monkeypatch.setattr(
        "app.copilot.patients.OpenDentalClient.from_connection",
        lambda *args, **kwargs: _client(),
    )
    monkeypatch.setattr("app.copilot.patients.list_opendental_directory", _fail)
    monkeypatch.setattr("app.copilot.patients.list_eligibility_queue", lambda *args, **kwargs: [])

    result = list_copilot_directory(object(), practice_id="partner_clinic")  # type: ignore[arg-type]
    assert result["opendental_connected"] is False
    assert result["patients"] == []
    assert result["opendental_error"] is not None
    assert result["opendental_error"]["code"] == "econnector_down"
