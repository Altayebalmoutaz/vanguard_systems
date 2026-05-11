"""Layer-1 tests: schema-adjacent preflight validation and deterministic errors."""

from __future__ import annotations

import unittest
from uuid import uuid4

from app.eligibility.models import (
    EligibilityRequest,
    Layer1ErrorCode,
    Layer1ValidationError,
    TriggerEvent,
)
from app.eligibility.services import run_eligibility_check_endpoint
from app.eligibility.triggers import layer0_supabase_validation


def _request() -> EligibilityRequest:
    return EligibilityRequest(
        patient_id=uuid4(),
        first_name="Ana",
        last_name="Patient",
        dob="1990-01-01",
        subscriber_id=" SUB-123 ",
        primary_payer_id="  testpayer  ",
        secondary_payer_id=None,
        cdt_codes=[" d0120 ", "bad"],
        trigger_event=TriggerEvent.NEW_PATIENT,
    )


class TestLayer1Validation(unittest.TestCase):
    def test_layer1_normalizes_and_filters_cdt(self) -> None:
        from unittest.mock import patch

        req = _request()

        with (
            patch("app.eligibility.triggers.get_supabase") as mock_get_sb,
            patch("app.eligibility.db.validate_dental_payer", return_value=True),
            patch("app.eligibility.db.fetch_existing_cdt_codes", return_value={"D0120"}),
        ):
            mock_get_sb.return_value = object()
            updated, warnings = layer0_supabase_validation(req)

        self.assertEqual(updated.primary_payer_id, "TESTPAYER")
        self.assertEqual(updated.cdt_codes, ["D0120"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("L1_INVALID_CDT_REMOVED|code=BAD|", warnings[0])

    def test_invalid_primary_payer_has_deterministic_code(self) -> None:
        from unittest.mock import patch

        req = _request()
        with (
            patch("app.eligibility.triggers.get_supabase") as mock_get_sb,
            patch("app.eligibility.db.validate_dental_payer", return_value=False),
        ):
            mock_get_sb.return_value = object()
            with self.assertRaises(Layer1ValidationError) as ctx:
                layer0_supabase_validation(req)

        self.assertEqual(ctx.exception.code, Layer1ErrorCode.INVALID_PRIMARY_PAYER)

    def test_invalid_secondary_payer_has_deterministic_code(self) -> None:
        from unittest.mock import patch

        req = _request().model_copy(update={"secondary_payer_id": "BAD2"})

        def _payer_ok(_supabase: object, payer: str) -> bool:
            return payer != "BAD2"

        with (
            patch("app.eligibility.triggers.get_supabase") as mock_get_sb,
            patch("app.eligibility.db.validate_dental_payer", side_effect=_payer_ok),
        ):
            mock_get_sb.return_value = object()
            with self.assertRaises(Layer1ValidationError) as ctx:
                layer0_supabase_validation(req)

        self.assertEqual(ctx.exception.code, Layer1ErrorCode.INVALID_SECONDARY_PAYER)

    def test_layer1_failure_does_not_call_stedi(self) -> None:
        from unittest.mock import MagicMock, patch

        req = _request()
        stedi = MagicMock()
        with (
            patch("app.eligibility.services.get_supabase", return_value=object()),
            patch("app.eligibility.triggers.get_supabase", return_value=object()),
            patch("app.eligibility.db.validate_dental_payer", return_value=False),
            patch("app.eligibility.services.call_stedi", stedi),
        ):
            with self.assertRaises(Layer1ValidationError):
                run_eligibility_check_endpoint(req)

        stedi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
