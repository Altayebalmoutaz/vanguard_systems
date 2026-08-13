from __future__ import annotations

from fastapi.testclient import TestClient

from app.eligibility.main import app
from app.eligibility.models import TriggerEvent


def test_from_opendental_route_enqueues_live_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.eligibility.main.get_neon_dsn", lambda *_a, **_k: "postgresql://test")
    monkeypatch.setattr(
        "app.eligibility.main.get_settings",
        lambda: type(
            "S",
            (),
            {
                "opendental_auto_poll_enabled": False,
                "eligibility_agent_api_key": "",
                "pilot_default_practice_id": "vgd_mock_brooklyn",
                "opendental_writeback_allowed": True,
                "opendental_write_benefits_grid_enabled": False,
                "opendental_base_url": "https://api.opendental.com/api/v1",
                "pilot_shadow_mode": False,
            },
        )(),
    )

    def fake_enqueue(*_a, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"id": "req-1", "pipeline_run_id": "run-1"}

    monkeypatch.setattr(
        "app.integrations.opendental.eligibility_enqueue.enqueue_od_eligibility_check",
        fake_enqueue,
    )
    monkeypatch.setattr(
        "app.integrations.opendental.connections_store.get_connection",
        lambda *_a, **_k: {
            "practice_id": "vgd_mock_brooklyn",
            "writeback_enabled": True,
            "writeback_full": True,
            "writeback_shadow_compare": False,
            "base_url": "https://api.opendental.com/api/v1",
            "customer_key_ref": "OD_CUSTOMER_KEY_VGD_BROOKLYN",
        },
    )
    monkeypatch.setattr(
        "app.eligibility.main.OpenDentalClient.from_connection",
        lambda *a, **k: object(),
    )

    client = TestClient(app)
    resp = client.post(
        "/eligibility/from-opendental",
        json={
            "pat_num": 1,
            "trigger_event": TriggerEvent.PRE_APPOINTMENT.value,
            "cdt_codes": ["D1110"],
            "write_back": True,
            "practice_id": "vgd_mock_brooklyn",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["primary"] is None
    assert payload["opendental"]["queued"] is True
    assert payload["opendental"]["request_id"] == "req-1"
    assert payload["opendental"]["pat_num"] == 1
    assert captured["pat_num"] == 1
    assert captured["practice_id"] == "vgd_mock_brooklyn"
