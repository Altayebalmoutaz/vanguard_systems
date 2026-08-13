"""Tests for retrieval gating, verifier pass, and confidence calibration."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.coding.autonomy import decide_tier
from app.coding.cache import cache_clear
from app.coding.calibration import ReliabilityBin, calibrate, fit_calibration_map
from app.coding.config import CodingSettings
from app.coding.reliability import (
    encounter_is_routine,
    is_high_stakes,
    needs_verification,
    should_use_retrieval,
)
from app.coding.schemas import AutonomyTier, CodingSuggestRequest, MissingInfoCode
from app.coding.service import run_coding_suggest
from app.config import Settings

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "golden" / "coding"


def _base_request() -> dict:
    return json.loads((FIXTURE / "scribe_crown_eval.json").read_text(encoding="utf-8"))["request"]


class TestCalibration(unittest.TestCase):
    def test_identity_without_map(self) -> None:
        self.assertAlmostEqual(calibrate(0.83), 0.83)
        self.assertEqual(calibrate(1.5), 1.0)
        self.assertEqual(calibrate(-0.2), 0.0)

    def test_linear_interpolation_with_map(self) -> None:
        cmap = [(0.0, 0.0), (0.5, 0.3), (1.0, 0.9)]
        self.assertAlmostEqual(calibrate(0.5, cmap), 0.3)
        self.assertAlmostEqual(calibrate(0.75, cmap), 0.6)  # midpoint of 0.3..0.9

    def test_fit_calibration_map_requires_support(self) -> None:
        bins = [
            ReliabilityBin(0.8, 0.9, 0.85, 0.7, count=5),
            ReliabilityBin(0.9, 1.0, 0.95, 0.9, count=50),
        ]
        self.assertEqual(fit_calibration_map(bins, min_count=20), [])
        bins[0] = ReliabilityBin(0.8, 0.9, 0.85, 0.7, count=40)
        self.assertEqual(fit_calibration_map(bins, min_count=20), [(0.85, 0.7), (0.95, 0.9)])


class TestRetrievalGating(unittest.TestCase):
    def _req(self, findings: list[list[str]]) -> CodingSuggestRequest:
        data = _base_request()
        data["procedures"] = [
            {"line_id": str(i), "findings": f, "planned_or_performed": "performed"}
            for i, f in enumerate(findings, start=1)
        ]
        return CodingSuggestRequest.model_validate(data)

    def test_routine_encounter(self) -> None:
        req = self._req([["periodic exam"], ["adult prophylaxis"], ["bitewing radiograph"]])
        self.assertTrue(encounter_is_routine(req))

    def test_non_routine_encounter(self) -> None:
        req = self._req([["porcelain crown"], ["periodic exam"]])
        self.assertFalse(encounter_is_routine(req))

    def test_retrieval_on_for_non_routine_even_in_fast(self) -> None:
        cfg = CodingSettings(coding_retrieval_default=True)
        crown = self._req([["porcelain crown prep"]])
        routine = self._req([["periodic exam"]])
        self.assertTrue(should_use_retrieval(crown, fast=True, cfg=cfg))
        self.assertFalse(should_use_retrieval(routine, fast=True, cfg=cfg))
        self.assertTrue(should_use_retrieval(routine, fast=False, cfg=cfg))


class TestVerifierGating(unittest.TestCase):
    def test_high_stakes_prefixes(self) -> None:
        cfg = CodingSettings()
        self.assertTrue(is_high_stakes("D2740", cfg))
        self.assertTrue(is_high_stakes("D3330", cfg))
        self.assertTrue(is_high_stakes("D4341", cfg))
        self.assertTrue(is_high_stakes("D4346", cfg))
        self.assertFalse(is_high_stakes("D0120", cfg))
        self.assertFalse(is_high_stakes("D1110", cfg))

    def test_needs_verification_respects_flag(self) -> None:
        off = CodingSettings(coding_verifier_enabled=False)
        on = CodingSettings(coding_verifier_enabled=True, coding_verifier_confidence_threshold=0.7)
        self.assertFalse(
            needs_verification(cdt_code="D2740", confidence=0.4, payer_conflict=False, cfg=off)
        )
        self.assertTrue(  # D4346 is always verified
            needs_verification(cdt_code="D4346", confidence=0.9, payer_conflict=False, cfg=off)
        )
        self.assertFalse(  # other perio codes still respect the global flag
            needs_verification(cdt_code="D4341", confidence=0.9, payer_conflict=False, cfg=off)
        )
        self.assertTrue(  # high stakes (including D43) when verifier is on
            needs_verification(cdt_code="D4341", confidence=0.99, payer_conflict=False, cfg=on)
        )
        self.assertTrue(  # high stakes
            needs_verification(cdt_code="D2740", confidence=0.99, payer_conflict=False, cfg=on)
        )
        self.assertTrue(  # low confidence
            needs_verification(cdt_code="D0120", confidence=0.5, payer_conflict=False, cfg=on)
        )
        self.assertFalse(  # routine + confident + no conflict
            needs_verification(cdt_code="D0120", confidence=0.9, payer_conflict=False, cfg=on)
        )


class TestAutonomyTiers(unittest.TestCase):
    def _cfg(self, **kw) -> CodingSettings:
        base = dict(
            coding_autonomy_enabled=True,
            coding_autonomy_auto_threshold=0.95,
            coding_autonomy_review_threshold=0.75,
        )
        base.update(kw)
        return CodingSettings(**base)

    def test_disabled_is_review(self) -> None:
        tier = decide_tier(
            cdt_code="D0120",
            calibrated_confidence=0.99,
            has_blocking_gap=False,
            is_valid=True,
            payer_conflict=False,
            cfg=self._cfg(coding_autonomy_enabled=False),
        )
        self.assertEqual(tier, AutonomyTier.review)

    def test_blocking_or_invalid_or_missing_is_ask(self) -> None:
        cfg = self._cfg()
        self.assertEqual(
            decide_tier(
                cdt_code=None,
                calibrated_confidence=0.99,
                has_blocking_gap=False,
                is_valid=False,
                payer_conflict=False,
                cfg=cfg,
            ),
            AutonomyTier.ask,
        )
        self.assertEqual(
            decide_tier(
                cdt_code="D0120",
                calibrated_confidence=0.99,
                has_blocking_gap=True,
                is_valid=True,
                payer_conflict=False,
                cfg=cfg,
            ),
            AutonomyTier.ask,
        )
        self.assertEqual(
            decide_tier(
                cdt_code="D9999",
                calibrated_confidence=0.99,
                has_blocking_gap=False,
                is_valid=False,
                payer_conflict=False,
                cfg=cfg,
            ),
            AutonomyTier.ask,
        )

    def test_low_confidence_is_ask(self) -> None:
        tier = decide_tier(
            cdt_code="D0120",
            calibrated_confidence=0.6,
            has_blocking_gap=False,
            is_valid=True,
            payer_conflict=False,
            cfg=self._cfg(),
        )
        self.assertEqual(tier, AutonomyTier.ask)

    def test_low_stakes_high_conf_is_auto(self) -> None:
        tier = decide_tier(
            cdt_code="D0120",
            calibrated_confidence=0.97,
            has_blocking_gap=False,
            is_valid=True,
            payer_conflict=False,
            cfg=self._cfg(),
        )
        self.assertEqual(tier, AutonomyTier.auto)

    def test_high_stakes_needs_allowlist_for_auto(self) -> None:
        cfg = self._cfg()
        self.assertEqual(
            decide_tier(
                cdt_code="D2740",
                calibrated_confidence=0.97,
                has_blocking_gap=False,
                is_valid=True,
                payer_conflict=False,
                cfg=cfg,
            ),
            AutonomyTier.review,
        )
        self.assertEqual(
            decide_tier(
                cdt_code="D2740",
                calibrated_confidence=0.97,
                has_blocking_gap=False,
                is_valid=True,
                payer_conflict=False,
                cfg=cfg,
                allowlist=frozenset({"D2740"}),
            ),
            AutonomyTier.auto,
        )

    def test_payer_conflict_blocks_auto(self) -> None:
        tier = decide_tier(
            cdt_code="D0120",
            calibrated_confidence=0.99,
            has_blocking_gap=False,
            is_valid=True,
            payer_conflict=True,
            cfg=self._cfg(),
        )
        self.assertEqual(tier, AutonomyTier.review)

    def test_mid_confidence_is_review(self) -> None:
        tier = decide_tier(
            cdt_code="D0120",
            calibrated_confidence=0.85,
            has_blocking_gap=False,
            is_valid=True,
            payer_conflict=False,
            cfg=self._cfg(),
        )
        self.assertEqual(tier, AutonomyTier.review)


class TestVerifierRepairInService(unittest.TestCase):
    def setUp(self) -> None:
        cache_clear()

    def test_verifier_repairs_high_stakes_line(self) -> None:
        data = _base_request()
        llm_value = {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2740",
                    "confidence": 0.72,
                    "explanation": "crown",
                    "icd10_codes": [],
                },
            ],
            "overall_confidence": 0.72,
            "justification": "crown",
        }
        with (
            patch(
                "app.coding.service.llm_generate_line_recommendations",
                return_value=llm_value,
            ),
            patch(
                "app.coding.service.verify_line",
                return_value={
                    "cdt_code": "D2750",
                    "confidence": 0.95,
                    "explanation": "PFM to high noble metal",
                    "changed": True,
                },
            ) as mock_verify,
            patch("app.coding.service.fetch_run_by_request_id", return_value=None),
            patch("app.coding.service.insert_coding_run", return_value=None),
            patch("app.coding.service.write_audit_log"),
            patch("app.coding.service.create_supabase", return_value=None),
        ):
            out = run_coding_suggest(
                CodingSuggestRequest.model_validate(data),
                settings=Settings(openrouter_api_key="x"),
                coding_settings=CodingSettings(
                    coding_verifier_enabled=True,
                    coding_confidence_review_threshold=0.5,
                ),
            )
        mock_verify.assert_called_once()
        self.assertEqual(out.recommendations[0].cdt_code, "D2750")
        self.assertTrue(any("Verifier changed line 1" in w for w in out.warnings))
        self.assertNotIn(
            MissingInfoCode.SURFACE_MISSING,
            {m.code for m in out.recommendations[0].missing_info},
        )


if __name__ == "__main__":
    unittest.main()
