"""Tests for pipeline executor run-type branches."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.config import Settings


class EligibilityExecutorTests(unittest.TestCase):
    @patch("app.pipeline.executor.complete_pipeline_run")
    @patch("app.pipeline.executor._execute_eligibility_request")
    @patch("app.pipeline.executor.write_audit_log")
    def test_eligibility_run_completes(
        self,
        _mock_audit: MagicMock,
        mock_execute: MagicMock,
        mock_complete: MagicMock,
    ) -> None:
        from app.pipeline.executor import execute_pipeline_run

        mock_execute.return_value = {
            "request_id": "req-1",
            "terminal_status": "completed",
        }
        settings = Settings(neon_database_url="postgresql://neon")
        run = {
            "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "practice_id": "practice-1",
            "run_type": "eligibility_request",
            "payload": {"request_id": "req-1"},
            "locked_by": "pipeline_worker",
        }

        execute_pipeline_run(settings, run)

        mock_execute.assert_called_once()
        mock_complete.assert_called_once()
        self.assertEqual(
            mock_complete.call_args.kwargs["result"]["terminal_status"],
            "completed",
        )


class EligibilityProcessorTests(unittest.TestCase):
    def test_classify_member_id_error(self) -> None:
        from app.eligibility.request_processor import actionable_error

        action = actionable_error("Invalid subscriber id for member lookup")
        self.assertEqual(action["error_code"], "INVALID_MEMBER_ID")
        self.assertEqual(action["terminal_status"], "needs_attention")

    def test_extract_check_id_from_primary(self) -> None:
        from app.eligibility.request_processor import extract_check_id

        result = {"primary": {"check_id": "abc-123"}}
        self.assertEqual(extract_check_id(result, "primary"), "abc-123")

    @patch("app.eligibility.request_processor.get_eligibility_settings")
    @patch("app.eligibility.request_processor.run_eligibility_check_endpoint")
    @patch("app.eligibility.request_processor.complete_eligibility_request_processing")
    @patch("app.eligibility.request_processor.lock_eligibility_request_for_processing")
    @patch("app.eligibility.request_processor.fetch_eligibility_request_row")
    @patch("app.eligibility.request_processor.insert_eligibility_request_event")
    @patch("app.eligibility.request_processor.touch_eligibility_agent_settings_sync")
    @patch("app.eligibility.request_processor.get_supabase")
    def test_process_happy_path(
        self,
        _mock_supabase: MagicMock,
        _mock_touch: MagicMock,
        _mock_event: MagicMock,
        mock_fetch: MagicMock,
        _mock_lock: MagicMock,
        mock_complete: MagicMock,
        mock_run: MagicMock,
        _mock_elig_settings: MagicMock,
    ) -> None:
        from app.eligibility.request_processor import process_eligibility_request

        request_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        patient_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        mock_fetch.return_value = {
            "id": str(request_id),
            "status": "queued",
            "attempt_count": 0,
            "max_attempts": 3,
            "patient_id": str(patient_id),
            "first_name": "Jane",
            "last_name": "Doe",
            "dob": "1990-01-01",
            "subscriber_id": "sub-1",
            "primary_payer_id": "payer-1",
            "cdt_codes": [],
            "trigger_event": "APPOINTMENT_BOOKED",
        }
        mock_run.return_value = {"primary": {"check_id": "chk-1"}}

        settings = Settings(neon_database_url="postgresql://neon")
        out = process_eligibility_request(
            settings,
            practice_id="practice-1",
            request_id=request_id,
            locked_by="pipeline_worker",
        )

        self.assertEqual(out["terminal_status"], "completed")
        self.assertEqual(out["primary_check_id"], "chk-1")
        mock_complete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
