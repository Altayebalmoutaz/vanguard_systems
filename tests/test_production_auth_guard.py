"""Production auth guardrails for the FastAPI app factory."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings
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

    def test_rejects_mock_provider_identity_for_production_stedi(self) -> None:
        eligibility_settings = SimpleNamespace(
            eligibility_agent_api_key="eligibility-key",
            stedi_api_key="stedi-key",
            provider_npi="1999999984",
            provider_name="Mock Dental Practice",
            provider_tax_id="123456789",
            voice_verification_enabled=False,
        )
        with (
            patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
            patch("app.eligibility.config.get_settings", return_value=eligibility_settings),
            self.assertRaises(RuntimeError) as ctx,
        ):
            validate_production_eligibility_security()
        self.assertIn("PROVIDER_NPI", str(ctx.exception))
        self.assertIn("PROVIDER_NAME", str(ctx.exception))
        self.assertIn("PROVIDER_TAX_ID", str(ctx.exception))

    def test_allows_real_provider_identity_for_production_stedi(self) -> None:
        eligibility_settings = SimpleNamespace(
            eligibility_agent_api_key="eligibility-key",
            stedi_api_key="stedi-key",
            provider_npi="1104023674",
            provider_name="Example Dental PLLC",
            provider_tax_id="987654321",
            voice_verification_enabled=False,
        )
        with (
            patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
            patch("app.eligibility.config.get_settings", return_value=eligibility_settings),
        ):
            validate_production_eligibility_security()


if __name__ == "__main__":
    unittest.main()
