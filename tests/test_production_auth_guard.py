"""Production auth guardrails for the FastAPI app factory."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.eligibility.config import EligibilitySettings
from app.startup_guards import (
    validate_production_auth,
    validate_production_eligibility_security,
)


class ProductionAuthGuardTests(unittest.TestCase):
    def test_allows_open_mode_outside_production(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            validate_production_auth(Settings(require_auth=False))

    def test_raises_when_production_without_require_auth(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                validate_production_auth(Settings(require_auth=False))
            self.assertIn("REQUIRE_AUTH", str(ctx.exception))

    def test_raises_when_production_without_require_rbac(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                validate_production_auth(Settings(require_auth=True, require_rbac=False))
            self.assertIn("REQUIRE_RBAC", str(ctx.exception))

    def test_raises_when_production_rbac_without_neon_url(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                validate_production_auth(
                    Settings(require_auth=True, require_rbac=True, neon_database_url="")
                )
            self.assertIn("NEON_DATABASE_URL", str(ctx.exception))

    def test_allows_production_when_auth_and_rbac_configured(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=False):
            validate_production_auth(
                Settings(
                    require_auth=True,
                    require_rbac=True,
                    neon_database_url="postgresql://user:pass@example.test/db",
                )
            )

    def test_raises_when_production_bland_webhook_secret_is_missing(self) -> None:
        eligibility_settings = EligibilitySettings(
            eligibility_agent_api_key="eligibility-key",
            voice_verification_enabled=True,
            voice_call_provider="bland",
            bland_api_key="bland-key",
            bland_webhook_signing_secret="",
            twilio_webhook_base_url="https://example.test/eligibility-agent",
        )
        with (
            patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
            patch(
                "app.eligibility.config.get_settings",
                return_value=eligibility_settings,
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                validate_production_eligibility_security()
        self.assertIn("BLAND_WEBHOOK_SIGNING_SECRET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
