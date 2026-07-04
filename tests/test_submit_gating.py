"""Tests for claim submit HITL gating."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import HTTPException

from app.config import Settings
from app.rcm.submit_gating import assert_claim_submission_allowed


class SubmitGatingTests(unittest.TestCase):
    @patch("app.rcm.submit_gating.get_neon_dsn", return_value=None)
    def test_skips_when_neon_unconfigured(self, _mock_dsn: MagicMock) -> None:
        assert_claim_submission_allowed(
            Settings(),
            practice_id="practice-1",
            claim_record_id="claim-1",
        )

    @patch("app.rcm.submit_gating._get_hitl_task_status", return_value="pending")
    @patch("app.rcm.submit_gating.get_neon_dsn", return_value="postgresql://neon")
    def test_blocks_pending_task(self, _mock_dsn: MagicMock, _mock_status: MagicMock) -> None:
        settings = Settings(neon_database_url="postgresql://neon")
        with self.assertRaises(HTTPException) as ctx:
            assert_claim_submission_allowed(
                settings,
                practice_id="practice-1",
                hitl_task_id="task-1",
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "hitl_review_pending")

    @patch("app.rcm.submit_gating._get_hitl_task_status", return_value="approved")
    @patch("app.rcm.submit_gating.get_neon_dsn", return_value="postgresql://neon")
    def test_allows_approved_task(self, _mock_dsn: MagicMock, _mock_status: MagicMock) -> None:
        settings = Settings(neon_database_url="postgresql://neon")
        assert_claim_submission_allowed(
            settings,
            practice_id="practice-1",
            hitl_task_id=str(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        )


if __name__ == "__main__":
    unittest.main()
