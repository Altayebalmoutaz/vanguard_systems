"""Tests for Wave 9 shadow pilot mode."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.config import Settings
from app.eligibility.config import EligibilitySettings
from app.pilot.shadow import opendental_writeback_allowed
from app.pilot.shadow_store import get_shadow_summary
from app.rcm.submit_gating import assert_claim_submission_allowed


class ShadowModeConfigTests(unittest.TestCase):
    def test_writeback_blocked_in_shadow_mode(self) -> None:
        settings = EligibilitySettings.model_construct(
            opendental_writeback_enabled=True,
            pilot_shadow_mode=True,
        )
        self.assertFalse(settings.opendental_writeback_allowed)

    def test_writeback_allowed_when_not_shadow(self) -> None:
        settings = EligibilitySettings.model_construct(
            opendental_writeback_enabled=True,
            pilot_shadow_mode=False,
        )
        self.assertTrue(settings.opendental_writeback_allowed)

    def test_helper_respects_shadow_flag(self) -> None:
        on = EligibilitySettings.model_construct(
            opendental_writeback_enabled=True,
            pilot_shadow_mode=True,
        )
        self.assertFalse(opendental_writeback_allowed(on))


class SubmitGatingShadowTests(unittest.TestCase):
    def test_blocks_claim_submit_in_shadow_mode(self) -> None:
        settings = Settings.model_construct(
            pilot_shadow_mode=True,
            neon_database_url="postgresql://neon",
        )
        with self.assertRaises(HTTPException) as ctx:
            assert_claim_submission_allowed(
                settings,
                practice_id="clinic-1",
                claim_record_id="claim-1",
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "pilot_shadow_mode")


class ShadowSummaryTests(unittest.TestCase):
    @patch("app.pilot.shadow_store.neon_connection")
    @patch("app.pilot.shadow_store.get_neon_dsn", return_value="postgresql://neon")
    def test_aggregates_metrics(self, _mock_dsn: MagicMock, mock_conn: MagicMock) -> None:
        rows = [
            {
                "event_type": "eligibility.checked",
                "match_status": "pending",
                "agent_payload": {"routing": {"status": "CLEARED"}},
                "human_label": None,
                "metadata": {},
            },
            {
                "event_type": "coding.reviewed",
                "match_status": "match",
                "agent_payload": {},
                "human_label": {"status": "approved"},
                "metadata": {},
            },
            {
                "event_type": "hitl.resolved",
                "match_status": "mismatch",
                "agent_payload": {"ai_codes": ["D1110"]},
                "human_label": {"action": "override", "final_codes": ["D1120"]},
                "metadata": {},
            },
        ]

        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        connection = MagicMock()
        connection.cursor.return_value = cursor
        connection.__enter__ = MagicMock(return_value=connection)
        connection.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value = connection

        settings = Settings(neon_database_url="postgresql://neon")
        summary = get_shadow_summary(settings, practice_id="clinic-1", days=7)

        self.assertEqual(summary["eligibility"]["total_checks"], 1)
        self.assertEqual(summary["eligibility"]["by_routing_status"]["CLEARED"], 1)
        self.assertEqual(summary["agent_accuracy"]["total_human_reviews"], 2)
        self.assertEqual(summary["agent_accuracy"]["matches"], 1)
        self.assertEqual(summary["agent_accuracy"]["mismatches"], 1)
        self.assertEqual(summary["hitl"]["tasks_resolved"], 1)
        self.assertEqual(summary["hitl"]["override_count"], 1)


if __name__ == "__main__":
    unittest.main()
