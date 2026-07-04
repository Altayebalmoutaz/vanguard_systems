"""Tests for decision_service Neon/Supabase routing."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.config import Settings
from app.services.decision_service import (
    _patient_age_from_dob,
    review_decision,
    run_agent_for_encounter,
)


class PatientAgeTests(unittest.TestCase):
    def test_age_from_dob(self) -> None:
        age = _patient_age_from_dob(date(2000, 1, 1))
        self.assertGreaterEqual(age, 20)


class DecisionServiceRoutingTests(unittest.TestCase):
    @patch("app.services.decision_service._call_coding_agent")
    @patch("app.services.decision_service._insert_decision_neon")
    @patch("app.services.decision_service._fetch_encounter_neon")
    @patch("app.services.decision_service._get_supabase_for_reference")
    @patch("app.services.decision_service.get_neon_dsn")
    def test_run_agent_uses_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_get_sb: MagicMock,
        mock_fetch: MagicMock,
        mock_insert: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_get_sb.return_value = MagicMock()
        mock_fetch.return_value = {
            "id": "enc-1",
            "clinical_note": "Patient presents for prophy",
            "patient_age": 30,
            "insurance": "Delta",
        }
        mock_insert.return_value = "dec-1"
        mock_agent.return_value = {
            "cdt_codes": ["D1110"],
            "icd10_codes": [],
            "confidence": 0.9,
            "justification": "Prophy",
        }
        settings = Settings(neon_database_url="postgresql://neon")

        out = run_agent_for_encounter(settings, "enc-1", practice_id="practice-1")

        self.assertEqual(out["decision_id"], "dec-1")
        mock_fetch.assert_called_once()
        mock_insert.assert_called_once()

    @patch("app.services.decision_service._fetch_encounter_neon")
    @patch("app.services.decision_service.get_neon_dsn")
    def test_run_agent_not_found(self, mock_dsn: MagicMock, mock_fetch: MagicMock) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_fetch.return_value = None
        settings = Settings(neon_database_url="postgresql://neon")

        with self.assertRaises(HTTPException) as ctx:
            run_agent_for_encounter(settings, "missing", practice_id="practice-1")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("app.services.decision_service._review_decision_neon")
    @patch("app.services.decision_service.get_neon_dsn")
    def test_review_uses_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_review: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_review.return_value = {"message": "Decision reviewed successfully"}
        settings = Settings(neon_database_url="postgresql://neon")

        out = review_decision(
            settings,
            "11111111-1111-1111-1111-111111111111",
            "approved",
            None,
            practice_id="practice-1",
        )

        self.assertEqual(out["message"], "Decision reviewed successfully")
        mock_review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
