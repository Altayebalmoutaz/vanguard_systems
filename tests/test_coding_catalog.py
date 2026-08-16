"""In-memory CDT/ICD catalog used by chairside suggest."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from app.coding.catalog import (
    catalog_is_loaded,
    cdt_metadata,
    clear_catalog,
    eval_fixture_catalog,
    eval_fixture_icd,
    seed_catalog,
    validate_codes,
)
from app.coding.config import CodingSettings
from app.coding.pending import clear_pending, peek_pending_by_id, remember_pending_run
from app.coding.schemas import CodingSuggestRequest, PatientInfo, ProcedureLine
from app.coding.service import run_coding_suggest
from app.coding.store import fetch_run_by_id
from app.config import Settings


class TestCatalogSeed(unittest.TestCase):
    def tearDown(self) -> None:
        clear_catalog()

    def test_unloaded_catalog_does_not_void(self) -> None:
        clear_catalog()
        result = validate_codes(["D9999"], [], supabase=None)
        self.assertFalse(result.loaded)
        self.assertEqual(result.invalid_cdt, frozenset())

    def test_seeded_catalog_flags_unknown_cdt(self) -> None:
        seed_catalog(eval_fixture_catalog(), eval_fixture_icd())
        self.assertTrue(catalog_is_loaded())
        result = validate_codes(["D0120", "D9999"], ["K02.9", "R51"], supabase=None)
        self.assertTrue(result.loaded)
        self.assertEqual(result.invalid_cdt, frozenset({"D9999"}))
        self.assertEqual(result.invalid_icd, frozenset({"R51"}))
        meta = cdt_metadata(["D0120"], supabase=None)
        self.assertIn("D0120", meta)


class TestVoidInvalidAndPersist(unittest.TestCase):
    def tearDown(self) -> None:
        clear_catalog()
        clear_pending()

    def test_invented_leftover_is_voided_when_catalog_seeded(self) -> None:
        seed_catalog(eval_fixture_catalog(), eval_fixture_icd())
        req = CodingSuggestRequest(
            request_id=uuid4(),
            practice_id="vgd_mock_brooklyn",
            patient_id="pat_1",
            provider_id="prov_1",
            encounter_datetime=datetime(2026, 8, 16, 9, 0, tzinfo=UTC),
            patient=PatientInfo(age=40),
            procedures=[
                ProcedureLine(
                    line_id="1",
                    findings=["custom occlusal guard delivered"],
                )
            ],
        )
        mock_llm = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D9999",
                    "confidence": 0.7,
                    "explanation": "invented",
                    "icd10_codes": [],
                }
            ],
            "overall_confidence": 0.7,
            "justification": "invented",
        }
        scheduled: list[object] = []
        with (
            patch(
                "app.coding.service.llm_generate_line_recommendations",
                return_value=mock_llm,
            ),
            patch("app.coding.service.fetch_run_by_request_id", return_value=None),
            patch("app.coding.service.insert_coding_run") as mock_insert,
            patch("app.coding.service.write_audit_log"),
            patch("app.coding.service.create_supabase", return_value=None),
        ):
            out = run_coding_suggest(
                req,
                settings=Settings(openrouter_api_key="x"),
                coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
                schedule_persist=scheduled.append,
            )
            self.assertIsNone(out.recommendations[0].cdt_code)
            self.assertEqual(out.status, "needs_info")
            self.assertIsInstance(out.coding_run_id, UUID)
            self.assertEqual(len(scheduled), 1)
            mock_insert.assert_not_called()
            scheduled[0]()
            mock_insert.assert_called_once()
            self.assertEqual(mock_insert.call_args.kwargs["coding_run_id"], out.coding_run_id)

    def test_pending_run_is_visible_to_decision_lookup(self) -> None:
        run_id = uuid4()
        request_id = uuid4()
        remember_pending_run(
            practice_id="vgd_mock_brooklyn",
            request_id=request_id,
            coding_run_id=run_id,
            payer_id="62308",
            response_payload={"recommendations": [{"line_id": "1", "cdt_code": "D0120"}]},
        )
        row = peek_pending_by_id("vgd_mock_brooklyn", run_id)
        self.assertIsNotNone(row)
        fetched = fetch_run_by_id(
            Settings(),
            practice_id="vgd_mock_brooklyn",
            coding_run_id=run_id,
        )
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["id"], str(run_id))


if __name__ == "__main__":
    unittest.main()
