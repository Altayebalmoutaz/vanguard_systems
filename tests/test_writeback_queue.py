"""Tests for OpenDental writeback pipeline queue."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.config import Settings
from app.pipeline.writeback_queue import (
    WRITEBACK_MAX_ATTEMPTS,
    build_opendental_writeback_payload,
    enqueue_opendental_writeback,
)


class WritebackQueueTests(unittest.TestCase):
    def test_build_payload_includes_pat_num(self) -> None:
        payload = build_opendental_writeback_payload(
            pat_num=42,
            primary_pat_plan_num=1,
            primary_plan_num=2,
            primary_ins_sub_num=3,
            primary_result={"check_id": "chk-1"},
        )
        self.assertEqual(payload["pat_num"], 42)
        self.assertEqual(payload["primary_result"]["check_id"], "chk-1")

    @patch("app.pipeline.writeback_queue.create_pipeline_run")
    def test_enqueue_uses_writeback_run_type(self, mock_create: MagicMock) -> None:
        mock_create.return_value = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        settings = Settings(neon_database_url="postgresql://neon")
        payload = build_opendental_writeback_payload(
            pat_num=1,
            primary_pat_plan_num=1,
            primary_plan_num=1,
            primary_ins_sub_num=1,
            primary_result={},
        )

        run_id = enqueue_opendental_writeback(
            settings,
            practice_id="practice-1",
            payload=payload,
            idempotency_key="od_writeback:practice-1:1:chk",
        )

        self.assertEqual(run_id, UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["run_type"], "opendental_writeback")
        self.assertEqual(kwargs["max_attempts"], WRITEBACK_MAX_ATTEMPTS)

    def test_enqueue_returns_none_without_neon(self) -> None:
        settings = Settings(neon_database_url=None)
        payload = build_opendental_writeback_payload(
            pat_num=1,
            primary_pat_plan_num=1,
            primary_plan_num=1,
            primary_ins_sub_num=1,
            primary_result={},
        )
        self.assertIsNone(
            enqueue_opendental_writeback(settings, practice_id="p1", payload=payload)
        )


class WritebackExecutorRetryTests(unittest.TestCase):
    @patch("app.pipeline.executor.complete_pipeline_run")
    @patch("app.pipeline.executor.fail_pipeline_run")
    @patch("app.pipeline.executor._execute_opendental_writeback")
    @patch("app.pipeline.executor.write_audit_log")
    def test_partial_failure_triggers_retry(
        self,
        _mock_audit: MagicMock,
        mock_execute: MagicMock,
        mock_fail: MagicMock,
        mock_complete: MagicMock,
    ) -> None:
        from app.pipeline.executor import execute_pipeline_run

        mock_execute.return_value = {
            "write_back_result": {},
            "partial_failure": True,
        }
        settings = Settings(neon_database_url="postgresql://neon")
        run = {
            "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "practice_id": "practice-1",
            "run_type": "opendental_writeback",
            "payload": {"pat_num": 1},
            "locked_by": "test_worker",
        }

        execute_pipeline_run(settings, run)

        mock_fail.assert_called_once()
        mock_complete.assert_not_called()
        self.assertTrue(mock_fail.call_args.kwargs.get("retry"))


if __name__ == "__main__":
    unittest.main()
