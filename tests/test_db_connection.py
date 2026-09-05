"""Tests for Neon PHI-plane connection helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.db.connection import NeonNotConfiguredError, get_neon_dsn, require_neon_dsn
from app.db.tenancy import set_tenant_gucs


class NeonDsnTests(unittest.TestCase):
    def test_get_neon_dsn_empty_when_unset(self) -> None:
        self.assertIsNone(get_neon_dsn(Settings(neon_database_url=None)))

    def test_get_neon_dsn_strips_whitespace(self) -> None:
        self.assertEqual(
            get_neon_dsn(Settings(neon_database_url="  postgresql://x  ")),
            "postgresql://x",
        )

    def test_require_neon_dsn_raises_when_missing(self) -> None:
        with self.assertRaises(NeonNotConfiguredError):
            require_neon_dsn(Settings(neon_database_url=""))


class SetTenantGucsTests(unittest.TestCase):
    def test_sets_session_scoped_gucs(self) -> None:
        cursor = MagicMock()
        set_tenant_gucs(cursor, practice_id="practice-a", bypass_rls=False)
        cursor.execute.assert_any_call(
            "select set_config('app.practice_id', %s, false)",
            ("practice-a",),
        )
        cursor.execute.assert_any_call(
            "select set_config('app.bypass_rls', %s, false)",
            ("false",),
        )

    def test_bypass_rls_true(self) -> None:
        cursor = MagicMock()
        set_tenant_gucs(cursor, practice_id="p1", bypass_rls=True)
        cursor.execute.assert_any_call(
            "select set_config('app.bypass_rls', %s, false)",
            ("true",),
        )


class NeonConnectionTests(unittest.TestCase):
    @patch("app.db.connection.psycopg.connect")
    def test_applies_tenant_context_when_practice_id_given(self, mock_connect: MagicMock) -> None:
        conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = conn
        settings = Settings(neon_database_url="postgresql://test")

        from app.db.connection import neon_connection

        with neon_connection(settings, practice_id="p1"):
            pass

        conn.cursor.assert_called()

    @patch("app.db.connection.psycopg.connect")
    def test_bypass_rls_applies_without_practice_id(self, mock_connect: MagicMock) -> None:
        conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = conn
        settings = Settings(neon_database_url="postgresql://test")

        from app.db.connection import neon_connection

        with neon_connection(settings, bypass_rls=True):
            pass

        conn.cursor.assert_called()


if __name__ == "__main__":
    unittest.main()
