"""Production auth guardrails for the FastAPI app factory."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.startup_guards import validate_production_auth


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


if __name__ == "__main__":
    unittest.main()
