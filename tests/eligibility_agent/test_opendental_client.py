from __future__ import annotations

import json
from pathlib import Path

import respx
from httpx import Response

from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.errors import OpenDentalConfigError
from app.integrations.opendental.models import ODInsVerifyCreate


def _client(*, replay_dir: str | None = None) -> OpenDentalClient:
    return OpenDentalClient(
        base_url="http://localhost:30222/api/v1",
        developer_key="dev",
        customer_key="cust",
        timeout_seconds=5.0,
        replay_dir=replay_dir,
    )


@respx.mock
def test_get_patient_uses_odfhir_header() -> None:
    route = respx.get("http://localhost:30222/api/v1/patients/1").mock(
        return_value=Response(
            200, json={"PatNum": 1, "FName": "A", "LName": "B", "Birthdate": "1970-01-01"}
        )
    )
    out = _client().get_patient(1)
    assert out.PatNum == 1
    assert route.called
    sent = route.calls[0].request.headers.get("Authorization")
    assert sent == "ODFHIR dev/cust"


@respx.mock
def test_create_insverify_put() -> None:
    route = respx.put("http://localhost:30222/api/v1/insverifies").mock(
        return_value=Response(
            200,
            json={
                "InsVerifyNum": 999,
                "DateLastVerified": "2026-05-11",
                "VerifyType": "PatientEnrollment",
                "FKey": 101,
                "Note": "ok",
            },
        )
    )
    out = _client().create_insverify(
        ODInsVerifyCreate(
            DateLastVerified="2026-05-11", VerifyType="PatientEnrollment", FKey=101, Note="ok"
        )
    )
    assert route.called
    assert out.InsVerifyNum == 999


def test_replay_mode_short_circuits_http(tmp_path: Path) -> None:
    fixtures = tmp_path / "od"
    fixtures.mkdir()
    (fixtures / "patient_1.json").write_text(
        json.dumps({"PatNum": 1, "FName": "A", "LName": "B", "Birthdate": "1970-01-01"}),
        encoding="utf-8",
    )
    c = _client(replay_dir=str(fixtures))
    out = c.get_patient(1)
    assert out.FName == "A"


def test_missing_keys_raise() -> None:
    try:
        OpenDentalClient(
            base_url="http://localhost:30222/api/v1",
            developer_key="",
            customer_key="",
            timeout_seconds=5.0,
        )
        assert False, "expected OpenDentalConfigError"
    except OpenDentalConfigError:
        pass
