from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.eligibility import main as eligibility_main


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        eligibility_main,
        "get_settings",
        lambda: SimpleNamespace(eligibility_agent_api_key="voice-api-key"),
    )
    test_app = FastAPI()

    @test_app.post("/{path:path}")
    def accept(path: str) -> dict[str, str]:
        return {"path": path}

    test_app.add_middleware(eligibility_main.EligibilityAgentApiKeyMiddleware)
    return TestClient(test_app)


@pytest.mark.parametrize(
    "path",
    [
        "/eligibility/voice/queue",
        "/eligibility/voice/queue-from-request",
        "/eligibility/voice/sessions/00000000-0000-0000-0000-000000000001/review",
    ],
)
def test_voice_mutations_require_api_key(client: TestClient, path: str) -> None:
    response = client.post(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "missing_or_invalid_bearer"}

    response = client.post(path, headers={"Authorization": "Bearer voice-api-key"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/eligibility/voice/bland/00000000-0000-0000-0000-000000000001",
        "/eligibility/voice/status/00000000-0000-0000-0000-000000000001",
        "/eligibility/voice/twiml/00000000-0000-0000-0000-000000000001",
        "/eligibility/voice/twiml/00000000-0000-0000-0000-000000000001/gather",
    ],
)
def test_provider_voice_callbacks_remain_public(client: TestClient, path: str) -> None:
    response = client.post(path)
    assert response.status_code == 200


def test_callback_detection_supports_mounted_app_path() -> None:
    assert eligibility_main._is_public_voice_callback(
        "/eligibility-agent/eligibility/voice/bland/session-id"
    )
    assert not eligibility_main._is_public_voice_callback(
        "/eligibility-agent/eligibility/voice/sessions/session-id/review"
    )
