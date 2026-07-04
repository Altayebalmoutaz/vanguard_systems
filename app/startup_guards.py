"""Fail-fast checks applied when the FastAPI app factory starts."""

from __future__ import annotations

import os

from app.config import Settings


def validate_production_auth(settings: Settings) -> None:
    """Require auth when ENVIRONMENT marks a production deployment."""
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    if env not in {"production", "prod"}:
        return

    if not settings.require_auth:
        raise RuntimeError(
            "REQUIRE_AUTH must be true when ENVIRONMENT=production. "
            "Set REQUIRE_AUTH=1 and configure SUPABASE_JWT_SECRET / INTERNAL_API_KEYS."
        )
    if not settings.require_rbac:
        raise RuntimeError(
            "REQUIRE_RBAC must be true when ENVIRONMENT=production. "
            "Set REQUIRE_RBAC=1 and configure NEON_DATABASE_URL."
        )
    if not settings.neon_database_url:
        raise RuntimeError(
            "NEON_DATABASE_URL must be configured when ENVIRONMENT=production and REQUIRE_RBAC=1."
        )
