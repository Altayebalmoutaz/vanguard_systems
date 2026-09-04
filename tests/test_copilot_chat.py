from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.config import Settings
from app.copilot.chat import run_copilot_chat
from app.integrations.opendental.client import OpenDentalClient
from app.security.phi import PhiScrubError

_FIXTURES = Path(__file__).parent / "fixtures" / "opendental"
_PATIENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _settings(**overrides: object) -> Settings:
    payload = {
        "openrouter_api_key": "test-key",
        "copilot_enabled": True,
        "copilot_scrub_phi": True,
        "copilot_max_tool_iterations": 4,
        "openrouter_model": "openai/gpt-4o-mini",
    }
    payload.update(overrides)
    return Settings(**payload)  # type: ignore[arg-type]


def _client() -> OpenDentalClient:
    return OpenDentalClient(
        base_url="http://localhost:30222/api/v1",
        developer_key="dev",
        customer_key="cust",
        timeout_seconds=5.0,
        replay_dir=str(_FIXTURES),
    )


def _profile() -> dict[str, object]:
    return {
        "patient": {"id": str(_PATIENT_ID), "first_name": "Aardvark", "last_name": "Dent"},
        "latest_eligibility_check": {"id": None, "is_active": True},
        "agent_runs": [],
    }


def _run(settings: Settings, monkeypatch: pytest.MonkeyPatch, llm_impl):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.copilot.chat.openrouter_chat_completion", llm_impl)
    return run_copilot_chat(
        settings,
        practice_id="practice-1",
        patient_id=_PATIENT_ID,
        messages=[{"role": "user", "content": "What coverage is on file?"}],
        profile=_profile(),
        od_client=_client(),
        od_pat_num=1,
    )


def test_tool_then_final_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_llm(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs["payload"])
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_patient_overview",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "Coverage looks active."}}]
        }

    result = _run(_settings(), monkeypatch, fake_llm)
    assert result.reply == "Coverage looks active."
    assert result.tool_trace == [{"name": "get_patient_overview", "args": {}}]
    assert calls[0]["max_tokens"] == 2048
    tool_payload = calls[1]["messages"][-1]
    assert tool_payload["role"] == "tool"
    assert "123-45-6789" not in str(tool_payload["content"])


def test_scrub_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_llm(**kwargs):  # type: ignore[no-untyped-def]
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "explain_carc_code",
                                    "arguments": '{"reason_code":"45"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "app.copilot.chat.scrub_for_llm",
        lambda payload: (_ for _ in ()).throw(PhiScrubError("blocked")),
    )
    with pytest.raises(PhiScrubError):
        _run(_settings(copilot_scrub_phi=True), monkeypatch, fake_llm)


def test_names_pass_when_scrub_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_llm(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs["payload"])
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_patient_overview",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Aardvark is active."}}]}

    result = _run(_settings(copilot_scrub_phi=False), monkeypatch, fake_llm)
    assert result.reply == "Aardvark is active."
    assert "Aardvark" in str(calls[1]["messages"][-1]["content"])
