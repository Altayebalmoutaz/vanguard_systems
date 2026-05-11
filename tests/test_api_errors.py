"""
Coverage for :mod:`app.api.errors` — sanitized HTTPException helper.

The helper has two contracts:

1. The body returned to the client never contains raw exception text — only
   the operator-supplied ``public_message`` (or a status-code default) plus an
   ``error_id`` for support correlation.
2. The full exception detail is logged once under that same ``error_id``,
   passed through ``app.security.phi.scrub_for_log`` so PHI never reaches the
   sink.
"""

from __future__ import annotations

import logging
import unittest

from fastapi import HTTPException

from app.api.errors import sanitized_http_exception


class SanitizedExceptionShape(unittest.TestCase):
    def test_returns_httpexception_with_status_and_error_id(self) -> None:
        exc = sanitized_http_exception(500, log_message="something blew up")
        self.assertIsInstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 500)
        self.assertIsInstance(exc.detail, dict)
        self.assertEqual(exc.detail["message"], "Internal server error")
        self.assertIn("error_id", exc.detail)
        # error_id is a 32-char hex uuid.
        self.assertEqual(len(exc.detail["error_id"]), 32)
        int(exc.detail["error_id"], 16)

    def test_uses_public_message_when_provided(self) -> None:
        exc = sanitized_http_exception(
            502, public_message="Upstream Stedi failure"
        )
        self.assertEqual(exc.detail["message"], "Upstream Stedi failure")

    def test_unknown_status_code_falls_back_to_generic_message(self) -> None:
        exc = sanitized_http_exception(418)
        self.assertEqual(exc.detail["message"], "Request failed")

    def test_extra_fields_added_to_detail_but_cannot_override_reserved_keys(self) -> None:
        exc = sanitized_http_exception(
            400,
            public_message="Validation failed",
            extra={
                "code": "INVALID_PRIMARY_PAYER",
                "field": "primary_payer_id",
                # These two should be ignored — they are reserved.
                "error_id": "spoofed-id",
                "message": "spoofed",
            },
        )
        self.assertEqual(exc.detail["code"], "INVALID_PRIMARY_PAYER")
        self.assertEqual(exc.detail["field"], "primary_payer_id")
        self.assertEqual(exc.detail["message"], "Validation failed")
        self.assertNotEqual(exc.detail["error_id"], "spoofed-id")


class LoggingBehaviour(unittest.TestCase):
    def test_logs_under_error_id_with_no_phi_in_message(self) -> None:
        with self.assertLogs("vanguard.api.errors", level="ERROR") as cm:
            exc = sanitized_http_exception(
                500,
                log_message="db row insert failed",
                exc=ValueError("contains member_id MEM12345 ssn 123-45-6789"),
            )
        joined = "\n".join(cm.output)
        # The error_id from the exception detail must appear in the log line so
        # support engineers can correlate user complaints back to the trace.
        self.assertIn(exc.detail["error_id"], joined)
        # The original SSN must NOT survive scrubbing.
        self.assertNotIn("123-45-6789", joined)

    def test_logs_without_exc_when_none_provided(self) -> None:
        with self.assertLogs("vanguard.api.errors", level="ERROR") as cm:
            exc = sanitized_http_exception(503, log_message="redis offline")
        joined = "\n".join(cm.output)
        self.assertIn("redis offline", joined)
        self.assertIn(exc.detail["error_id"], joined)

    def test_log_message_is_scrubbed(self) -> None:
        with self.assertLogs("vanguard.api.errors", level="ERROR") as cm:
            sanitized_http_exception(
                500,
                log_message="failed for ssn 123-45-6789",
            )
        joined = "\n".join(cm.output)
        self.assertNotIn("123-45-6789", joined)


class ClientFacingDetailNeverLeaksException(unittest.TestCase):
    """The whole point of this helper: clients never see raw exception text."""

    def test_repr_of_exception_not_in_client_detail(self) -> None:
        secret_blob = "patient_dob=1987-03-15 member_id=MEM12345"
        # Suppress the noisy ERROR-level log line so the test output stays clean.
        logging.getLogger("vanguard.api.errors").setLevel(logging.CRITICAL)
        try:
            exc = sanitized_http_exception(
                500,
                public_message="Coding agent failed",
                log_message="downstream LLM blew up",
                exc=RuntimeError(secret_blob),
            )
        finally:
            logging.getLogger("vanguard.api.errors").setLevel(logging.NOTSET)
        rendered = str(exc.detail)
        self.assertNotIn("MEM12345", rendered)
        self.assertNotIn("1987-03-15", rendered)
        self.assertEqual(exc.detail["message"], "Coding agent failed")


if __name__ == "__main__":
    unittest.main()
