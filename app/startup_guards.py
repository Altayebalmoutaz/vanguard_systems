"""Fail-fast checks applied when the FastAPI app factory starts."""

from __future__ import annotations

import os

from app.config import Settings


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}


def validate_production_auth(settings: Settings) -> None:
    """Require auth when ENVIRONMENT marks a production deployment."""
    if not _is_production():
        return

    if not settings.require_auth:
        raise RuntimeError(
            "REQUIRE_AUTH must be true when ENVIRONMENT=production. "
            "Set REQUIRE_AUTH=1 and configure SUPABASE_JWT_SECRET / INTERNAL_API_KEYS."
        )
    if not settings.require_rbac:
        raise RuntimeError(
            "REQUIRE_RBAC must be true when ENVIRONMENT=production. "
            "Set REQUIRE_RBAC=1 and configure DATABASE_URL."
        )
    if not settings.neon_database_url:
        raise RuntimeError(
            "NEON_DATABASE_URL (or DATABASE_URL) must be configured when "
            "ENVIRONMENT=production and REQUIRE_RBAC=1."
        )


def validate_production_workers(settings: Settings) -> None:
    """In production the pipeline worker must run, or queued requests silently rot."""
    if not _is_production():
        return
    if not settings.pipeline_worker_enabled:
        raise RuntimeError(
            "PIPELINE_WORKER_ENABLED must be true when ENVIRONMENT=production; "
            "otherwise dashboard-submitted eligibility requests are never processed."
        )


def validate_production_eligibility_security() -> None:
    """Fail closed on eligibility sub-app auth and voice webhook verification.

    Imported lazily so the base app does not depend on eligibility settings at
    module import time.
    """
    if not _is_production():
        return
    from app.eligibility.config import get_settings as get_eligibility_settings

    elig = get_eligibility_settings()
    if not (elig.eligibility_agent_api_key or "").strip():
        raise RuntimeError(
            "ELIGIBILITY_AGENT_API_KEY must be set when ENVIRONMENT=production; "
            "without it the eligibility sub-app accepts unauthenticated requests."
        )
    if elig.voice_verification_enabled and elig.voice_call_provider == "twilio":
        if not (elig.twilio_auth_token or "").strip():
            raise RuntimeError(
                "TWILIO_AUTH_TOKEN must be set when voice verification is enabled in "
                "production with VOICE_CALL_PROVIDER=twilio; without it Twilio webhook "
                "signatures cannot be validated."
            )
        try:
            import twilio  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "The 'twilio' package must be installed when voice verification is "
                "enabled in production with VOICE_CALL_PROVIDER=twilio (webhook signature "
                "validation fails open without it)."
            ) from exc
    if elig.voice_verification_enabled and (elig.voice_call_provider or "bland").strip().lower() == "bland":
        if not (elig.bland_api_key or "").strip():
            raise RuntimeError(
                "BLAND_API_KEY must be set when VOICE_VERIFICATION_ENABLED and "
                "VOICE_CALL_PROVIDER=bland in production."
            )
        if not (elig.twilio_webhook_base_url or "").strip():
            raise RuntimeError(
                "VOICE_WEBHOOK_BASE_URL (or TWILIO_WEBHOOK_BASE_URL) must be set when "
                "Bland voice is enabled in production so call-completion webhooks reach the API."
            )


def validate_production_readiness(settings: Settings) -> None:
    """All production fail-fast checks; called from the app factory."""
    validate_production_auth(settings)
    validate_production_workers(settings)
    validate_production_eligibility_security()
