"""Voice queue respects Bland infra + dashboard agent settings."""

from __future__ import annotations

from types import SimpleNamespace

from app.eligibility.voice import queue as mod


def test_voice_infra_ready_bland() -> None:
    settings = SimpleNamespace(
        voice_call_provider="bland",
        bland_api_key="key",
        twilio_webhook_base_url="https://api.example.com/eligibility-agent",
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_from_number="",
    )
    assert mod.voice_infra_ready(settings) is True


def test_voice_enabled_requires_dashboard_toggle() -> None:
    settings = SimpleNamespace(
        voice_verification_enabled=True,
        voice_call_provider="bland",
        bland_api_key="key",
        twilio_webhook_base_url="https://api.example.com/eligibility-agent",
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_from_number="",
    )
    assert mod._voice_enabled(settings, None) is False
    assert mod._voice_enabled(settings, {"voice_verification_enabled": False}) is False
    assert mod._voice_enabled(settings, {"voice_verification_enabled": True}) is True


def test_voice_disabled_when_env_stack_off() -> None:
    settings = SimpleNamespace(
        voice_verification_enabled=False,
        voice_call_provider="bland",
        bland_api_key="key",
        twilio_webhook_base_url="https://api.example.com/eligibility-agent",
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_from_number="",
    )
    assert mod._voice_enabled(settings, {"voice_verification_enabled": True}) is False
