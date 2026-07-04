"""Tests for claim intake snapshot Neon/Supabase routing."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.integrations.claim_snapshots import fetch_claim_intake_snapshot


class ClaimSnapshotRoutingTests(unittest.TestCase):
    @patch("app.integrations.claim_snapshots._fetch_claim_snapshot_neon")
    @patch("app.integrations.claim_snapshots.get_neon_dsn")
    def test_prefers_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_neon: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon.return_value = {"encounter_id": "e1", "ready_for_claim": True}
        settings = Settings(neon_database_url="postgresql://neon")

        out = fetch_claim_intake_snapshot(settings, "e1", practice_id="practice-1")

        self.assertEqual(out["encounter_id"], "e1")
        mock_neon.assert_called_once()

    @patch("app.integrations.claim_snapshots._fetch_claim_snapshot_supabase")
    @patch("app.integrations.claim_snapshots.create_supabase")
    @patch("app.integrations.claim_snapshots.get_neon_dsn")
    def test_falls_back_to_supabase(
        self,
        mock_dsn: MagicMock,
        mock_create: MagicMock,
        mock_sb: MagicMock,
    ) -> None:
        mock_dsn.return_value = None
        mock_create.return_value = MagicMock()
        mock_sb.return_value = {"encounter_id": "e2"}
        settings = Settings(supabase_url="https://x.supabase.co", supabase_service_role_key="k")

        out = fetch_claim_intake_snapshot(settings, "e2", practice_id="practice-1")

        self.assertEqual(out["encounter_id"], "e2")
        mock_sb.assert_called_once()


if __name__ == "__main__":
    unittest.main()
