"""Tests for eligibility db Neon/Supabase routing."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.config import Settings
from app.db.phi_store import PhiStoreError
from app.eligibility.db import (
    fetch_retryable_requests,
    get_latest_eligibility_check,
    insert_audit_log,
    insert_eligibility_check,
)


class EligibilityDbRoutingTests(unittest.TestCase):
    @patch("app.eligibility.db_phi._insert_eligibility_check_neon")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_insert_prefers_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_neon_insert: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon_insert.return_value = UUID("11111111-1111-1111-1111-111111111111")
        settings = Settings(neon_database_url="postgresql://neon")
        supabase = MagicMock()

        check_id = insert_eligibility_check(
            supabase,
            {"patient_id": "33333333-3333-3333-3333-333333333333", "payer_id": "DELTA"},
            practice_id="practice-1",
            settings=settings,
        )

        self.assertEqual(check_id, UUID("11111111-1111-1111-1111-111111111111"))
        mock_neon_insert.assert_called_once()
        supabase.table.assert_not_called()

    @patch("app.eligibility.db_phi._insert_eligibility_check_supabase")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_insert_falls_back_to_supabase(
        self,
        mock_dsn: MagicMock,
        mock_sb_insert: MagicMock,
    ) -> None:
        mock_dsn.return_value = None
        mock_sb_insert.return_value = UUID("22222222-2222-2222-2222-222222222222")
        supabase = MagicMock()

        check_id = insert_eligibility_check(
            supabase,
            {"patient_id": "33333333-3333-3333-3333-333333333333", "payer_id": "DELTA"},
            settings=Settings(),
        )

        self.assertEqual(check_id, UUID("22222222-2222-2222-2222-222222222222"))
        mock_sb_insert.assert_called_once()

    @patch("app.eligibility.db_phi._insert_eligibility_check_supabase")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_insert_neon_without_practice_id_raises(
        self,
        mock_dsn: MagicMock,
        mock_sb_insert: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        supabase = MagicMock()

        with self.assertRaises(PhiStoreError):
            insert_eligibility_check(
                supabase,
                {"patient_id": "33333333-3333-3333-3333-333333333333", "payer_id": "DELTA"},
                settings=Settings(neon_database_url="postgresql://neon"),
            )

        mock_sb_insert.assert_not_called()

    @patch("app.eligibility.db_phi._get_latest_eligibility_check_neon")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_read_prefers_neon_when_practice_id_present(
        self,
        mock_dsn: MagicMock,
        mock_neon_read: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon_read.return_value = {"id": "check-1"}
        supabase = MagicMock()
        patient_id = UUID("33333333-3333-3333-3333-333333333333")

        row = get_latest_eligibility_check(
            supabase,
            patient_id,
            "DELTA",
            practice_id="practice-1",
            settings=Settings(neon_database_url="postgresql://neon"),
        )

        self.assertEqual(row, {"id": "check-1"})
        mock_neon_read.assert_called_once()

    @patch("app.eligibility.db_phi._fetch_retryable_requests_neon")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_retry_fetch_uses_neon_with_bypass(
        self,
        mock_dsn: MagicMock,
        mock_neon_fetch: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon_fetch.return_value = [{"id": "req-1", "status": "retrying"}]
        supabase = MagicMock()

        rows = fetch_retryable_requests(
            supabase,
            now_iso="2026-06-15T12:00:00Z",
            limit=5,
            settings=Settings(neon_database_url="postgresql://neon"),
        )

        self.assertEqual(len(rows), 1)
        mock_neon_fetch.assert_called_once()

    @patch("app.eligibility.db_phi._insert_audit_log_neon")
    @patch("app.eligibility.db_phi.get_neon_dsn")
    def test_audit_log_neon_requires_practice_id(
        self,
        mock_dsn: MagicMock,
        mock_neon_audit: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        supabase = MagicMock()

        insert_audit_log(
            supabase,
            patient_id=UUID("33333333-3333-3333-3333-333333333333"),
            event_type="ROUTING",
            detail={"status": "CLEARED"},
            practice_id="practice-1",
            settings=Settings(neon_database_url="postgresql://neon"),
        )

        mock_neon_audit.assert_called_once()
        supabase.table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
