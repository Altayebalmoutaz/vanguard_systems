"""Tests for fail-closed PHI scrubbing before LLM egress."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.security.phi import PhiScrubError, scrub_for_llm, scrub_for_log


class ScrubForLlmFailClosedTests(unittest.TestCase):
    def test_scrubs_nested_payload(self) -> None:
        payload = {"note": "Patient John Doe SSN 123-45-6789", "codes": ["D0120"]}
        out = scrub_for_llm(payload)
        self.assertIn("<REDACTED_SSN>", out["note"])
        self.assertEqual(out["codes"], ["D0120"])

    def test_presidio_runtime_failure_raises(self) -> None:
        with patch("app.security.phi._apply_presidio_scrub", side_effect=RuntimeError("boom")):
            with self.assertRaises(PhiScrubError):
                scrub_for_llm("Patient Jane Doe")

    def test_scrub_for_log_stays_lenient_on_presidio_failure(self) -> None:
        with patch("app.security.phi._apply_presidio_scrub", side_effect=RuntimeError("boom")):
            out = scrub_for_log("123-45-6789")
        self.assertIn("<REDACTED_SSN>", out)


if __name__ == "__main__":
    unittest.main()
