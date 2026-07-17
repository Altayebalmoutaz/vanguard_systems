"""Bearer guard for mounted eligibility sub-app (Supabase Edge → FastAPI)."""

import hashlib
import hmac
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.eligibility.config import get_settings as get_eligibility_settings
from app.main import create_app

BLAND_WEBHOOK_SECRET = "test-bland-webhook-secret"


@pytest.fixture
def client_with_eligibility_key(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    get_eligibility_settings.cache_clear()
    key = "test-eligibility-key-" + uuid.uuid4().hex
    monkeypatch.setenv("ELIGIBILITY_AGENT_API_KEY", key)
    monkeypatch.setenv("BLAND_WEBHOOK_SIGNING_SECRET", BLAND_WEBHOOK_SECRET)
    get_eligibility_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client, key
    get_eligibility_settings.cache_clear()
    monkeypatch.delenv("ELIGIBILITY_AGENT_API_KEY", raising=False)


def test_eligibility_health_ok_without_authorization_when_key_set(
    client_with_eligibility_key: tuple[TestClient, str],
) -> None:
    client, _ = client_with_eligibility_key
    r = client.get("/eligibility-agent/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_eligibility_check_requires_bearer_when_key_set(
    client_with_eligibility_key: tuple[TestClient, str],
) -> None:
    client, _ = client_with_eligibility_key
    r = client.post(
        "/eligibility-agent/eligibility/check",
        json={},
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_eligibility_check_accepts_matching_bearer(
    client_with_eligibility_key: tuple[TestClient, str],
) -> None:
    client, key = client_with_eligibility_key
    body = {
        "patient_id": str(uuid.uuid4()),
        "first_name": "A",
        "last_name": "B",
        "dob": "1990-01-01",
        "subscriber_id": "SUB1",
        "primary_payer_id": "PAYER_NONEXISTENT_FOR_TEST",
        "trigger_event": "APPOINTMENT_BOOKED",
    }
    with patch(
        "app.eligibility.main.run_eligibility_check_endpoint",
        return_value={
            "cached": False,
            "layer0_warnings": [],
            "primary": None,
            "secondary": None,
            "record": None,
        },
    ):
        r = client.post(
            "/eligibility-agent/eligibility/check",
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    assert r.status_code == 200


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/eligibility-agent/eligibility/voice/queue", {"check_id": str(uuid.uuid4())}),
        (
            f"/eligibility-agent/eligibility/voice/sessions/{uuid.uuid4()}/review",
            {"action": "approve"},
        ),
        (f"/eligibility-agent/eligibility/voice/twiml/{uuid.uuid4()}", {}),
    ],
)
def test_voice_control_routes_require_bearer(
    client_with_eligibility_key: tuple[TestClient, str],
    path: str,
    body: dict[str, str],
) -> None:
    client, _ = client_with_eligibility_key
    r = client.post(path, json=body)
    assert r.status_code == 401


def test_bland_webhook_requires_provider_signature_not_bearer(
    client_with_eligibility_key: tuple[TestClient, str],
) -> None:
    client, _ = client_with_eligibility_key
    path = f"/eligibility-agent/eligibility/voice/bland/{uuid.uuid4()}"
    body = b'{"category":"call"}'

    unsigned = client.post(path, content=body, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 403
    assert unsigned.json()["detail"] == "invalid_bland_signature"

    signature = hmac.new(
        BLAND_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    signed = client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
        },
    )
    assert signed.status_code == 200
    assert signed.json() == {"ok": "true", "ignored": "event"}


def test_eligibility_guard_off_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    get_eligibility_settings.cache_clear()
    monkeypatch.delenv("ELIGIBILITY_AGENT_API_KEY", raising=False)
    get_eligibility_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            body = {
                "patient_id": str(uuid.uuid4()),
                "first_name": "A",
                "last_name": "B",
                "dob": "1990-01-01",
                "subscriber_id": "SUB1",
                "primary_payer_id": "PAYER_X",
                "trigger_event": "APPOINTMENT_BOOKED",
            }
            with patch(
                "app.eligibility.main.run_eligibility_check_endpoint",
                return_value={
                    "cached": False,
                    "layer0_warnings": [],
                    "primary": None,
                    "secondary": None,
                    "record": None,
                },
            ):
                r = client.post("/eligibility-agent/eligibility/check", json=body)
            assert r.status_code == 200
    finally:
        get_eligibility_settings.cache_clear()
