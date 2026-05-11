"""Layer-2 tests for Stedi API client behavior."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.eligibility.api_client import call_stedi, call_stedi_batch
from app.eligibility.config import EligibilitySettings
from app.eligibility.models import StediAPIError


def _settings() -> EligibilitySettings:
    return EligibilitySettings.model_validate(
        {
            "STEDI_API_KEY": "test-key",
            "STEDI_TIMEOUT_SECONDS": 0.1,
            "STEDI_BATCH_TIMEOUT_SECONDS": 0.1,
            "STEDI_MAX_RETRIES": 3,
            "STEDI_RETRY_BASE_SECONDS": 0.0,
            "STEDI_RETRY_JITTER_SECONDS": 0.0,
        }
    )


def _resp(status_code: int, payload: object, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text or str(payload)
    if isinstance(payload, Exception):
        r.json.side_effect = payload
    else:
        r.json.return_value = payload
    return r


class TestApiClient(unittest.TestCase):
    def test_call_stedi_retries_then_succeeds(self) -> None:
        s = _settings()
        first = _resp(500, {"error": "x"}, "server error")
        second = _resp(200, {"ok": True}, '{"ok":true}')
        with (
            patch("app.eligibility.api_client.httpx.Client") as mock_client,
            patch("app.eligibility.api_client.time.sleep") as _sleep,
        ):
            client = mock_client.return_value.__enter__.return_value
            client.post.side_effect = [first, second]
            out = call_stedi({"foo": "bar"}, settings=s)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(client.post.call_count, 2)

    def test_call_stedi_retries_http_200_connectivity_aaa_then_succeeds(self) -> None:
        s = _settings()
        first = _resp(
            200,
            {"errors": [{"code": "42", "description": "Unable to Respond at Current Time"}]},
            '{"errors":[{"code":"42"}]}',
        )
        second = _resp(200, {"benefitsInformation": []}, '{"benefitsInformation":[]}')
        with (
            patch("app.eligibility.api_client.httpx.Client") as mock_client,
            patch("app.eligibility.api_client.time.sleep") as _sleep,
        ):
            client = mock_client.return_value.__enter__.return_value
            client.post.side_effect = [first, second]
            out = call_stedi({"foo": "bar"}, settings=s)
        self.assertEqual(out, {"benefitsInformation": []})
        self.assertEqual(client.post.call_count, 2)

    def test_call_stedi_does_not_retry_http_400_aaa_79(self) -> None:
        s = _settings()
        bad = _resp(
            400,
            {"errors": [{"code": "79", "description": "Invalid Participant Identification"}]},
            '{"errors":[{"code":"79"}]}',
        )
        with patch("app.eligibility.api_client.httpx.Client") as mock_client:
            client = mock_client.return_value.__enter__.return_value
            client.post.return_value = bad
            with self.assertRaises(StediAPIError):
                call_stedi({"foo": "bar"}, settings=s)
        self.assertEqual(client.post.call_count, 1)

    def test_call_stedi_raises_on_non_object_json(self) -> None:
        s = _settings()
        bad = _resp(200, ["not", "dict"], '["not","dict"]')
        with patch("app.eligibility.api_client.httpx.Client") as mock_client:
            client = mock_client.return_value.__enter__.return_value
            client.post.return_value = bad
            with self.assertRaises(StediAPIError):
                call_stedi({"foo": "bar"}, settings=s)

    def test_call_stedi_batch_raises_on_non_json(self) -> None:
        s = _settings()
        bad = _resp(200, ValueError("no json"), "<html>bad gateway</html>")
        with patch("app.eligibility.api_client.httpx.Client") as mock_client:
            client = mock_client.return_value.__enter__.return_value
            client.post.return_value = bad
            with self.assertRaises(StediAPIError):
                call_stedi_batch([{"foo": "bar"}], settings=s)


if __name__ == "__main__":
    unittest.main()
