"""Unit tests for the scribe-facing coding suggest API (LLM mocked)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.coding.adapter import (
    build_clinical_note,
    map_flat_codes_to_lines,
    patient_age,
    structured_prompt_block,
)
from app.coding.cache import cache_clear
from app.coding.config import CodingSettings
from app.coding.errors import CodingPersistenceError
from app.coding.gaps import (
    has_blocking,
    post_check_line,
    pre_check_line,
    pre_check_request,
)
from app.coding.main import app as coding_app
from app.coding.schemas import (
    CodingSuggestRequest,
    MissingInfoCode,
    ProcedureLine,
)
from app.coding.service import run_coding_suggest
from app.config import Settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "coding_suggest_request.json"


class TestCodingSuggestSchemas(unittest.TestCase):
    def test_fixture_loads(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        req = CodingSuggestRequest.model_validate(data)
        self.assertEqual(req.practice_id, "vgd_mock_brooklyn")
        self.assertEqual(len(req.procedures), 2)
        self.assertEqual(req.procedures[0].surfaces, ["M", "O"])

    def test_duplicate_line_ids_rejected(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["procedures"][1]["line_id"] = "1"
        with self.assertRaises(ValidationError):
            CodingSuggestRequest.model_validate(data)


class TestCodingGaps(unittest.TestCase):
    def test_restorative_pre_check_requires_tooth_surface(self) -> None:
        line = ProcedureLine(
            line_id="1",
            findings=["interproximal caries"],
            tooth_numbers=[],
            surfaces=[],
        )
        missing = pre_check_line(line)
        codes = {m.code for m in missing}
        self.assertIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_post_check_radiograph(self) -> None:
        line = ProcedureLine(line_id="1", tooth_numbers=["14"], surfaces=["O"])
        missing = post_check_line(
            line,
            cdt_code="D0220",
            attachments_present=[],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={"requires_radiograph": True},
        )
        self.assertTrue(any(m.code == MissingInfoCode.RADIOGRAPH_MISSING for m in missing))

    def test_global_payer_missing(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["payer"] = {}
        req = CodingSuggestRequest.model_validate(data)
        missing = pre_check_request(req)
        self.assertTrue(any(m.code == MissingInfoCode.PAYER_MISSING for m in missing))

    def test_negated_finding_is_not_restorative(self) -> None:
        # "no decay noted" must not trigger tooth/surface gaps (negation-aware).
        line = ProcedureLine(
            line_id="1",
            findings=["no decay noted"],
            tooth_numbers=[],
            surfaces=[],
        )
        codes = {m.code for m in pre_check_line(line)}
        self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_crown_finding_requires_tooth_not_surface(self) -> None:
        line = ProcedureLine(
            line_id="1",
            findings=["porcelain crown restoration"],
            tooth_numbers=[],
            surfaces=[],
        )
        codes = {m.code for m in pre_check_line(line)}
        self.assertIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_post_check_db_precedence_suppresses_surface_for_crown(self) -> None:
        line = ProcedureLine(line_id="1", tooth_numbers=["14"], surfaces=[])
        missing = post_check_line(
            line,
            cdt_code="D2740",
            attachments_present=[],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={
                "requires_tooth": True,
                "requires_surfaces": False,
                "requires_radiograph": False,
            },
        )
        codes = {m.code for m in missing}
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)
        self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)

    def test_post_check_fallback_keeps_surface_for_unknown_filling(self) -> None:
        # No DB metadata row -> code-range fallback still flags a filling surface.
        line = ProcedureLine(line_id="1", tooth_numbers=["14"], surfaces=[])
        missing = post_check_line(
            line,
            cdt_code="D2391",
            attachments_present=[],
            confidence=0.9,
            threshold=0.75,
            cdt_meta=None,
        )
        self.assertTrue(any(m.code == MissingInfoCode.SURFACE_MISSING for m in missing))

    def test_radiograph_gap_is_advisory_not_blocking(self) -> None:
        line = ProcedureLine(line_id="1", tooth_numbers=["3"], surfaces=[])
        missing = post_check_line(
            line,
            cdt_code="D2740",
            attachments_present=[],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={
                "requires_tooth": True,
                "requires_surfaces": False,
                "requires_radiograph": True,
            },
        )
        codes = {m.code for m in missing}
        self.assertIn(MissingInfoCode.RADIOGRAPH_MISSING, codes)
        self.assertFalse(has_blocking(missing))


class TestCodingAdapter(unittest.TestCase):
    def test_build_clinical_note_includes_tooth(self) -> None:
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        note = build_clinical_note(req)
        self.assertIn("tooth=14", note)
        self.assertIn("surfaces=M, O", note)

    def test_structured_prompt_excludes_patient_identifier(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["patient_id"] = "PAT-12345"
        req = CodingSuggestRequest.model_validate(data)

        prompt = structured_prompt_block(req)

        self.assertNotIn("patient_id", prompt)
        self.assertNotIn("PAT-12345", prompt)

    def test_missing_patient_age_remains_unknown(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["patient"] = {}
        req = CodingSuggestRequest.model_validate(data)

        self.assertIsNone(patient_age(req))
        self.assertIn("- patient_age: unknown", structured_prompt_block(req))

    def test_map_flat_codes_to_lines(self) -> None:
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        mapped = map_flat_codes_to_lines(
            req,
            cdt_codes=["D2392", "D0120"],
            icd10_codes=["K02.9"],
            confidence=0.9,
            justification="ok",
        )
        self.assertEqual(mapped[0]["cdt_code"], "D2392")
        self.assertEqual(mapped[1]["cdt_code"], "D0120")

    def test_map_flat_codes_does_not_duplicate_last_code(self) -> None:
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

        mapped = map_flat_codes_to_lines(
            req,
            cdt_codes=["D2392"],
            icd10_codes=["K02.9"],
            confidence=0.9,
            justification="partial fallback",
        )

        self.assertEqual(mapped[0]["cdt_code"], "D2392")
        self.assertIsNone(mapped[1]["cdt_code"])


class TestCodingSuggestService(unittest.TestCase):
    def setUp(self) -> None:
        cache_clear()

    @patch("app.coding.service.insert_coding_run")
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id", return_value=None)
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_line_level_happy_path(
        self,
        mock_llm: MagicMock,
        _sb: MagicMock,
        _fetch: MagicMock,
        _audit: MagicMock,
        mock_insert: MagicMock,
    ) -> None:
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2392",
                    "confidence": 0.91,
                    "explanation": "Two-surface posterior composite for caries #14 MO",
                    "icd10_codes": ["K02.9"],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0120",
                    "confidence": 0.88,
                    "explanation": "Periodic oral evaluation",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.9,
            "justification": "Restorative + eval",
        }
        run_id = UUID("22222222-2222-2222-2222-222222222222")
        mock_insert.return_value = run_id
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        settings = Settings(openrouter_api_key="test-key")
        cfg = CodingSettings(coding_confidence_review_threshold=0.75)
        out = run_coding_suggest(req, settings=settings, coding_settings=cfg)
        self.assertEqual(out.coding_run_id, run_id)
        self.assertEqual(len(out.recommendations), 2)
        self.assertEqual(out.recommendations[0].cdt_code, "D2392")
        self.assertEqual(out.recommendations[1].cdt_code, "D0120")
        self.assertFalse(out.idempotent_replay)
        # attachments include bitewing → no RADIOGRAPH_MISSING on restorative line
        self.assertFalse(
            any(
                m.code == MissingInfoCode.RADIOGRAPH_MISSING
                for m in out.recommendations[0].missing_info
            )
        )
        mock_insert.assert_called_once()

    @patch("app.coding.service.insert_coding_run", return_value=None)
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id", return_value=None)
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_configured_persistence_failure_aborts_suggest(
        self,
        mock_llm: MagicMock,
        _sb: MagicMock,
        _fetch: MagicMock,
        mock_audit: MagicMock,
        _insert: MagicMock,
    ) -> None:
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2392",
                    "confidence": 0.9,
                    "explanation": "composite",
                    "icd10_codes": [],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0120",
                    "confidence": 0.9,
                    "explanation": "eval",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.9,
            "justification": "ok",
        }
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

        with self.assertRaises(CodingPersistenceError):
            run_coding_suggest(
                req,
                settings=Settings(
                    openrouter_api_key="test-key",
                    neon_database_url="postgresql://configured",
                ),
            )

        mock_audit.assert_not_called()

    @patch("app.coding.service.insert_coding_run", return_value=None)
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id", return_value=None)
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.apply_payer_rules_tool")
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_suppresses_unmatched_insurance_payer_warning(
        self,
        mock_llm: MagicMock,
        mock_payer: MagicMock,
        _sb: MagicMock,
        _fetch: MagicMock,
        _audit: MagicMock,
        _insert: MagicMock,
    ) -> None:
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2140",
                    "confidence": 0.9,
                    "explanation": "amalgam",
                    "icd10_codes": [],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0120",
                    "confidence": 0.9,
                    "explanation": "eval",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.9,
            "justification": "ok",
        }
        mock_payer.return_value = {
            "payer_flags": [
                (
                    "Payer rules: rules were returned but none matched encounter insurance "
                    "('Cigna'). Align encounter.insurance with payer_name, or use payer_name "
                    "'*' / 'any' for payer-wide notices."
                ),
                "[payer_rules][Cigna] D2140 (documentation_required): pre-op radiograph",
            ],
            "payer_rules_matched": [],
        }
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        out = run_coding_suggest(
            req,
            settings=Settings(openrouter_api_key="test-key"),
            coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
        )
        self.assertFalse(any("none matched encounter insurance" in w for w in out.warnings))
        self.assertTrue(any("pre-op radiograph" in w for w in out.warnings))

    @patch("app.coding.service.insert_coding_run", return_value=None)
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id", return_value=None)
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.apply_payer_rules_tool")
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_unknown_age_is_not_treated_as_newborn_for_payer_rules(
        self,
        mock_llm: MagicMock,
        mock_payer: MagicMock,
        _sb: MagicMock,
        _fetch: MagicMock,
        _audit: MagicMock,
        _insert: MagicMock,
    ) -> None:
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2392",
                    "confidence": 0.9,
                    "explanation": "composite",
                    "icd10_codes": [],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0120",
                    "confidence": 0.9,
                    "explanation": "eval",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.9,
            "justification": "ok",
        }
        mock_payer.return_value = {"payer_flags": [], "payer_rules_matched": []}
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["patient"] = {}

        run_coding_suggest(
            CodingSuggestRequest.model_validate(data),
            settings=Settings(openrouter_api_key="test-key"),
        )

        self.assertIsNone(mock_payer.call_args.args[3])

    @patch("app.coding.service.insert_coding_run")
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id")
    @patch("app.coding.service.create_supabase", return_value=None)
    def test_idempotent_replay(
        self,
        _sb: MagicMock,
        mock_fetch: MagicMock,
        _audit: MagicMock,
        mock_insert: MagicMock,
    ) -> None:
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        prior = {
            "id": "33333333-3333-3333-3333-333333333333",
            "response_payload": {
                "schema_version": "1.0",
                "request_id": str(req.request_id),
                "coding_run_id": "33333333-3333-3333-3333-333333333333",
                "status": "pending_review",
                "recommendations": [
                    {
                        "line_id": "1",
                        "cdt_code": "D2392",
                        "confidence": 0.9,
                        "explanation": "cached",
                        "icd10_codes": [],
                        "required_supporting_documentation": [],
                        "missing_info": [],
                    }
                ],
                "global_missing_info": [],
                "warnings": [],
                "overall_confidence": 0.9,
                "idempotent_replay": False,
            },
        }
        mock_fetch.return_value = prior
        out = run_coding_suggest(req, settings=Settings(openrouter_api_key="x"))
        self.assertTrue(out.idempotent_replay)
        self.assertEqual(out.recommendations[0].cdt_code, "D2392")
        mock_insert.assert_not_called()

    @patch(
        "app.coding.service.insert_coding_run",
        return_value=UUID("44444444-4444-4444-4444-444444444444"),
    )
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id")
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_concurrent_duplicate_returns_persisted_winner(
        self,
        mock_llm: MagicMock,
        _sb: MagicMock,
        mock_fetch: MagicMock,
        _audit: MagicMock,
        mock_insert: MagicMock,
    ) -> None:
        req = CodingSuggestRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2140",
                    "confidence": 0.8,
                    "explanation": "later result",
                    "icd10_codes": [],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0150",
                    "confidence": 0.8,
                    "explanation": "later result",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.8,
            "justification": "later result",
        }
        persisted_payload = {
            "schema_version": "1.0",
            "request_id": str(req.request_id),
            "coding_run_id": None,
            "status": "pending_review",
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2392",
                    "confidence": 0.9,
                    "explanation": "persisted winner",
                    "icd10_codes": [],
                    "required_supporting_documentation": [],
                    "missing_info": [],
                },
                {
                    "line_id": "2",
                    "cdt_code": "D0120",
                    "confidence": 0.9,
                    "explanation": "persisted winner",
                    "icd10_codes": [],
                    "required_supporting_documentation": [],
                    "missing_info": [],
                },
            ],
            "global_missing_info": [],
            "warnings": [],
            "overall_confidence": 0.9,
            "idempotent_replay": False,
        }
        mock_fetch.side_effect = [
            None,
            {
                "id": "44444444-4444-4444-4444-444444444444",
                "response_payload": persisted_payload,
            },
        ]

        out = run_coding_suggest(
            req,
            settings=Settings(openrouter_api_key="x"),
        )

        self.assertTrue(out.idempotent_replay)
        self.assertEqual(out.recommendations[0].cdt_code, "D2392")
        self.assertEqual(out.recommendations[1].cdt_code, "D0120")
        mock_insert.assert_called_once()

    @patch("app.coding.service.insert_coding_run", return_value=None)
    @patch("app.coding.service.write_audit_log")
    @patch("app.coding.service.fetch_run_by_request_id", return_value=None)
    @patch("app.coding.service.create_supabase", return_value=None)
    @patch("app.coding.service.llm_generate_line_recommendations")
    def test_needs_info_when_surface_missing(
        self,
        mock_llm: MagicMock,
        _sb: MagicMock,
        _fetch: MagicMock,
        _audit: MagicMock,
        _insert: MagicMock,
    ) -> None:
        mock_llm.return_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2391",
                    "confidence": 0.8,
                    "explanation": "one surface",
                    "icd10_codes": ["K02.9"],
                }
            ],
            "overall_confidence": 0.8,
            "justification": "ok",
        }
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["procedures"] = [
            {
                "line_id": "1",
                "tooth_numbers": ["14"],
                "surfaces": [],
                "findings": ["caries"],
                "planned_or_performed": "planned",
            }
        ]
        data["attachments_present"] = ["bitewing_radiograph"]
        req = CodingSuggestRequest.model_validate(data)
        out = run_coding_suggest(
            req,
            settings=Settings(openrouter_api_key="x"),
            coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
        )
        self.assertEqual(out.status, "needs_info")
        self.assertTrue(
            any(
                m.code == MissingInfoCode.SURFACE_MISSING
                for m in out.recommendations[0].missing_info
            )
        )


class TestCodingGapGateRegressions(unittest.TestCase):
    """Regressions for the false-needs_info pilot bug (crowns + negated recall)."""

    def setUp(self) -> None:
        cache_clear()

    def _run(self, data: dict, mock_llm_value: dict):
        with (
            patch(
                "app.coding.service.llm_generate_line_recommendations",
                return_value=mock_llm_value,
            ),
            patch("app.coding.service.fetch_run_by_request_id", return_value=None),
            patch("app.coding.service.insert_coding_run", return_value=None),
            patch("app.coding.service.write_audit_log"),
            patch("app.coding.service.create_supabase", return_value=None),
        ):
            return run_coding_suggest(
                CodingSuggestRequest.model_validate(data),
                settings=Settings(openrouter_api_key="x"),
                coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
            )

    def test_negated_recall_stays_pending_review(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["procedures"] = [
            {
                "line_id": "1",
                "findings": ["periodic oral evaluation; no decay noted"],
                "planned_or_performed": "performed",
            },
            {
                "line_id": "2",
                "findings": ["adult prophylaxis; no decay noted"],
                "planned_or_performed": "performed",
            },
        ]
        data["supporting_note"] = "Recall visit. No decay noted. Prophy and exam."
        out = self._run(
            data,
            {
                "recommendations": [
                    {
                        "line_id": "1",
                        "cdt_code": "D0120",
                        "confidence": 0.9,
                        "explanation": "eval",
                        "icd10_codes": [],
                    },
                    {
                        "line_id": "2",
                        "cdt_code": "D1110",
                        "confidence": 0.9,
                        "explanation": "prophy",
                        "icd10_codes": [],
                    },
                ],
                "overall_confidence": 0.9,
                "justification": "recall",
            },
        )
        self.assertEqual(out.status, "pending_review")
        self.assertEqual(out.recommendations[0].cdt_code, "D0120")
        for rec in out.recommendations:
            codes = {m.code for m in rec.missing_info}
            self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)
            self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_crown_without_surfaces_stays_pending_review(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["procedures"] = [
            {
                "line_id": "1",
                "tooth_numbers": ["3"],
                "surfaces": [],
                "findings": ["porcelain-fused-to-metal crown"],
                "planned_or_performed": "planned",
            },
        ]
        data["attachments_present"] = ["periapical_radiograph"]
        out = self._run(
            data,
            {
                "recommendations": [
                    {
                        "line_id": "1",
                        "cdt_code": "D2750",
                        "confidence": 0.9,
                        "explanation": "crown",
                        "icd10_codes": [],
                    },
                ],
                "overall_confidence": 0.9,
                "justification": "crown",
            },
        )
        self.assertEqual(out.status, "pending_review")
        self.assertNotIn(
            MissingInfoCode.SURFACE_MISSING,
            {m.code for m in out.recommendations[0].missing_info},
        )

    def test_advisory_only_gaps_stay_pending_review(self) -> None:
        # Missing payer + age are advisory: valid codes must stay reviewable.
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["payer"] = {}
        data["patient"] = {}
        data["procedures"] = [
            {
                "line_id": "1",
                "findings": ["periodic oral evaluation"],
                "planned_or_performed": "performed",
            },
        ]
        out = self._run(
            data,
            {
                "recommendations": [
                    {
                        "line_id": "1",
                        "cdt_code": "D0120",
                        "confidence": 0.9,
                        "explanation": "eval",
                        "icd10_codes": [],
                    },
                ],
                "overall_confidence": 0.9,
                "justification": "eval",
            },
        )
        self.assertEqual(out.status, "pending_review")
        advisory = {m.code for m in out.global_missing_info}
        self.assertIn(MissingInfoCode.PAYER_MISSING, advisory)
        self.assertIn(MissingInfoCode.AGE_MISSING, advisory)


class TestCodingHttpApi(unittest.TestCase):
    def test_health(self) -> None:
        client = TestClient(coding_app)
        res = client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["service"], "coding-agent")

    @patch("app.coding.main.run_coding_suggest")
    def test_suggest_endpoint(self, mock_run: MagicMock) -> None:
        from app.coding.schemas import CodingSuggestResponse, LineRecommendation

        req_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        mock_run.return_value = CodingSuggestResponse(
            request_id=UUID(req_data["request_id"]),
            status="pending_review",
            recommendations=[
                LineRecommendation(
                    line_id="1",
                    cdt_code="D2392",
                    confidence=0.9,
                    explanation="ok",
                )
            ],
            overall_confidence=0.9,
        )
        client = TestClient(coding_app)
        res = client.post("/v1/suggest", json=req_data)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["recommendations"][0]["cdt_code"], "D2392")
        mock_run.assert_called_once()

    @patch(
        "app.coding.main.run_coding_suggest",
        side_effect=CodingPersistenceError("database unavailable"),
    )
    def test_suggest_endpoint_returns_503_for_persistence_failure(
        self, _mock_run: MagicMock
    ) -> None:
        req_data = json.loads(FIXTURE.read_text(encoding="utf-8"))

        res = TestClient(coding_app).post("/v1/suggest", json=req_data)

        self.assertEqual(res.status_code, 503)
        self.assertIn("could not be saved", res.json()["detail"]["message"])


if __name__ == "__main__":
    unittest.main()
