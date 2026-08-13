"""Tests for OD writeback service HTTP client + executor flag branch."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.integrations.opendental.writeback_client import (
    OpenDentalWritebackServiceError,
    call_opendental_writeback_service,
)


class WritebackClientTests(unittest.TestCase):
    def test_requires_url(self) -> None:
        settings = Settings(odwb_service_url="", odwb_api_key="k")
        with self.assertRaises(OpenDentalWritebackServiceError):
            call_opendental_writeback_service(settings, body={})

    @patch("app.integrations.opendental.writeback_client.httpx.Client")
    def test_posts_bearer_and_returns_json(self, mock_client_cls: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "write_back_result": {"ok": True},
            "partial_failure": False,
            "audit_events": [],
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        settings = Settings(
            odwb_service_url="http://odwb:8080",
            odwb_api_key="secret",
            odwb_timeout_seconds=12.0,
        )
        out = call_opendental_writeback_service(settings, body={"source_agent": "eligibility"})
        self.assertEqual(out["write_back_result"], {"ok": True})
        kwargs = mock_client.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(mock_client.post.call_args.args[0], "http://odwb:8080/v1/writeback")


class WritebackExecutorFlagTests(unittest.TestCase):
    @patch("app.pipeline.executor._execute_opendental_writeback_via_service")
    @patch("app.pipeline.executor.get_app_settings")
    @patch("app.pipeline.executor.get_eligibility_settings")
    def test_flag_routes_to_service(
        self,
        mock_elig: MagicMock,
        mock_app: MagicMock,
        mock_via: MagicMock,
    ) -> None:
        from app.pipeline.executor import _execute_opendental_writeback

        mock_app.return_value = Settings(odwb_service_url="http://odwb:8080", odwb_api_key="k")
        mock_elig.return_value = MagicMock()
        mock_via.return_value = {
            "write_back_result": {},
            "partial_failure": False,
            "dry_run_financial": False,
            "coverage_order": "primary",
        }
        payload = {
            "pat_num": 1,
            "primary_pat_plan_num": 1,
            "primary_plan_num": 1,
            "primary_ins_sub_num": 1,
            "primary_result": {},
        }
        out = _execute_opendental_writeback(payload)
        mock_via.assert_called_once()
        self.assertFalse(out["partial_failure"])

    @patch("app.pipeline.executor.run_opendental_writeback")
    @patch("app.pipeline.executor.OpenDentalClient")
    @patch("app.pipeline.executor.get_app_settings")
    @patch("app.pipeline.executor.get_eligibility_settings")
    def test_flag_unset_uses_inprocess(
        self,
        mock_elig: MagicMock,
        mock_app: MagicMock,
        mock_client_cls: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        from app.pipeline.executor import _execute_opendental_writeback

        mock_app.return_value = Settings(odwb_service_url="")
        mock_elig.return_value = MagicMock()
        mock_client_cls.from_settings.return_value = MagicMock()
        mock_run.return_value = {"benefit_notes": {"ins_sub_num": 1}}
        payload = {
            "pat_num": 1,
            "primary_pat_plan_num": 1,
            "primary_plan_num": 1,
            "primary_ins_sub_num": 1,
            "primary_result": {},
        }
        out = _execute_opendental_writeback(payload)
        mock_run.assert_called_once()
        self.assertIn("write_back_result", out)


if __name__ == "__main__":
    unittest.main()
