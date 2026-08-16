"""Unit tests for deterministic chairside CDT proposers."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.coding.config import CodingSettings
from app.coding.propose import propose
from app.coding.schemas import CodingSuggestRequest, PatientInfo, ProcedureLine
from app.coding.service import run_coding_suggest
from app.config import Settings

SAMPLE_02B = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "fixtures"
    / "coding-suggest-scribe-sample-02b-host-filled.json"
)


def _line(**kw) -> ProcedureLine:
    data = {
        "line_id": "1",
        "tooth_numbers": [],
        "surfaces": [],
        "findings": [],
        "planned_or_performed": "performed",
    }
    data.update(kw)
    return ProcedureLine.model_validate(data)


def _request(
    procedures: list[ProcedureLine],
    *,
    age: int | None = 42,
) -> CodingSuggestRequest:
    return CodingSuggestRequest(
        request_id=uuid4(),
        practice_id="vgd_mock_brooklyn",
        patient_id="pat_1",
        provider_id="prov_1",
        encounter_datetime=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        patient=PatientInfo(age=age),
        procedures=procedures,
    )


def _code(request: CodingSuggestRequest, line_id: str = "1") -> str | None:
    by_id = {p.line_id: p for p in propose(request)}
    hit = by_id[line_id]
    if not hit.resolved:
        return "__unresolved__"
    return hit.cdt_code


class TestExamRules(unittest.TestCase):
    def test_periodic_exam(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["periodic oral evaluation"])])),
            "D0120",
        )

    def test_comprehensive_exam(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["comprehensive examination", "new patient"])])),
            "D0150",
        )

    def test_perio_eval(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["periodontal evaluation with periodontal charting"])])),
            "D0180",
        )


class TestImagingRules(unittest.TestCase):
    def test_fmx(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["full mouth set of x-rays taken and reviewed"])])),
            "D0210",
        )

    def test_four_bitewings(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["four bitewings taken"])])),
            "D0274",
        )

    def test_periapical(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["periapical radiograph of tooth 14"])])),
            "D0220",
        )

    def test_panoramic(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["panoramic radiograph"])])),
            "D0330",
        )


class TestPreventiveRules(unittest.TestCase):
    def test_adult_prophy(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["adult prophylaxis"])], age=42)),
            "D1110",
        )

    def test_child_prophy_by_age(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["prophylaxis"])], age=8)),
            "D1120",
        )

    def test_fluoride_varnish(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["fluoride varnish applied"])])),
            "D1206",
        )

    def test_ohi(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["oral hygiene instruction"])])),
            "D1330",
        )


class TestPerioRules(unittest.TestCase):
    def test_srp_quadrant(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            findings=[
                                "quadrant: UR (upper right)",
                                "non-surgical periodontal therapy",
                            ]
                        )
                    ]
                )
            ),
            "D4341",
        )

    def test_srp_one_to_three_teeth(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["3", "4"],
                            findings=["scaling and root planing"],
                        )
                    ]
                )
            ),
            "D4342",
        )

    def test_irrigation(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["gingival irrigation"])])),
            "D4921",
        )

    def test_srp_four_quadrants_units_and_icd(self) -> None:
        req = _request([_line(findings=["scaling and root planing, 4 quadrants"])])
        hit = propose(req)[0]
        self.assertTrue(hit.resolved)
        self.assertEqual(hit.cdt_code, "D4341")
        self.assertIn("× 4", hit.explanation)
        self.assertEqual(list(hit.icd10_codes), ["K05.30"])

    def test_periodontal_maintenance(self) -> None:
        req = _request([_line(findings=["periodontal maintenance"], planned_or_performed="planned")])
        hit = propose(req)[0]
        self.assertEqual(hit.cdt_code, "D4910")
        self.assertEqual(list(hit.icd10_codes), ["K05.30"])


class TestSealantEndoExtraction(unittest.TestCase):
    def test_sealants_two_teeth(self) -> None:
        req = _request(
            [
                _line(
                    tooth_numbers=["3", "14"],
                    surfaces=["O"],
                    findings=["deep pits and fissures"],
                )
            ]
        )
        hit = propose(req)[0]
        self.assertEqual(hit.cdt_code, "D1351")
        self.assertIn("× 2", hit.explanation)

    def test_molar_rct_from_irreversible_pulpitis(self) -> None:
        req = _request(
            [_line(tooth_numbers=["30"], findings=["irreversible pulpitis"])]
        )
        hit = propose(req)[0]
        self.assertEqual(hit.cdt_code, "D3330")
        self.assertEqual(list(hit.icd10_codes), ["K04.02"])

    def test_simple_extraction_from_vertical_root_fracture(self) -> None:
        req = _request(
            [_line(tooth_numbers=["8"], findings=["vertical root fracture"])]
        )
        hit = propose(req)[0]
        self.assertEqual(hit.cdt_code, "D7140")
        self.assertEqual(list(hit.icd10_codes), ["K03.81"])

    def test_socket_preservation_graft(self) -> None:
        req = _request(
            [_line(tooth_numbers=["8"], findings=["socket preservation bone graft"])]
        )
        hit = propose(req)[0]
        self.assertEqual(hit.cdt_code, "D7953")
        self.assertEqual(list(hit.icd10_codes), [])

    def test_surgical_extraction_stays_unresolved(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["32"],
                            findings=["surgical extraction of impacted tooth"],
                        )
                    ]
                )
            ),
            "__unresolved__",
        )


class TestCrownRules(unittest.TestCase):
    def test_porcelain_crown(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["30"],
                            findings=["full porcelain crown"],
                            planned_or_performed="planned",
                        )
                    ]
                )
            ),
            "D2740",
        )

    def test_pfm_crown(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["3"],
                            findings=["porcelain-fused-to-metal crown"],
                            planned_or_performed="planned",
                        )
                    ]
                )
            ),
            "D2750",
        )

    def test_porcelain_not_blocked_by_existing_amalgam_on_another_finding(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["30"],
                            findings=[
                                "full porcelain crown",
                                "existing MOD amalgam fractured in multiple areas",
                            ],
                            planned_or_performed="planned",
                        )
                    ]
                )
            ),
            "D2740",
        )

    def test_existing_gold_without_planned_material_is_null(self) -> None:
        self.assertIsNone(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["15"],
                            findings=[
                                "replacement of existing full gold crown",
                                "existing full gold crown present at #15",
                            ],
                            planned_or_performed="planned",
                        )
                    ]
                )
            )
        )


class TestFillingRules(unittest.TestCase):
    def test_posterior_composite_two_surfaces(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["14"],
                            surfaces=["M", "O"],
                            findings=["interproximal caries"],
                        )
                    ]
                )
            ),
            "D2392",
        )

    def test_anterior_composite_one_surface(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["8"],
                            surfaces=["M"],
                            findings=["composite restoration"],
                        )
                    ]
                )
            ),
            "D2330",
        )

    def test_amalgam_two_surfaces(self) -> None:
        self.assertEqual(
            _code(
                _request(
                    [
                        _line(
                            tooth_numbers=["19"],
                            surfaces=["D", "O"],
                            findings=["amalgam filling"],
                        )
                    ]
                )
            ),
            "D2150",
        )

    def test_negated_decay_is_unresolved(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["no decay noted"], surfaces=["O"], tooth_numbers=["14"])])),
            "__unresolved__",
        )

    def test_caries_without_surfaces_is_unresolved(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["interproximal caries"], tooth_numbers=["14"])])),
            "__unresolved__",
        )


class TestExplicitNulls(unittest.TestCase):
    def test_ppe(self) -> None:
        self.assertIsNone(
            _code(_request([_line(findings=["high-level PPE utilized to minimize aerosol risk"])]))
        )

    def test_preprocedural_rinse(self) -> None:
        self.assertIsNone(
            _code(_request([_line(findings=["pre-procedural rinse with molecular iodine"])]))
        )

    def test_laser_adjunct(self) -> None:
        self.assertIsNone(
            _code(
                _request(
                    [
                        _line(
                            findings=[
                                "laser-assisted periodontal therapy",
                                "adjunct to non-surgical periodontal therapy",
                            ]
                        )
                    ]
                )
            )
        )


class TestUnresolvedLeftovers(unittest.TestCase):
    def test_unknown_procedure_stays_unresolved(self) -> None:
        self.assertEqual(
            _code(_request([_line(findings=["custom occlusal guard delivered"])])),
            "__unresolved__",
        )

    def test_leftover_line_is_the_only_llm_input(self) -> None:
        req = _request(
            [
                _line(line_id="1", findings=["periodic oral evaluation"]),
                _line(line_id="2", findings=["custom occlusal guard delivered"]),
            ]
        )
        mock_llm = MagicMock(
            return_value={
                "recommendations": [
                    {
                        "line_id": "2",
                        "cdt_code": "D9944",
                        "confidence": 0.8,
                        "explanation": "occlusal guard",
                        "icd10_codes": [],
                    }
                ],
                "overall_confidence": 0.8,
                "justification": "leftover",
            }
        )
        with (
            patch("app.coding.service.llm_generate_line_recommendations", mock_llm),
            patch("app.coding.service.fetch_run_by_request_id", return_value=None),
            patch("app.coding.service.insert_coding_run", return_value=None),
            patch("app.coding.service.write_audit_log"),
            patch("app.coding.service.create_supabase", return_value=None),
        ):
            out = run_coding_suggest(
                req,
                settings=Settings(openrouter_api_key="x"),
                coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
            )
        mock_llm.assert_called_once()
        self.assertEqual(mock_llm.call_args.kwargs["line_ids"], ["2"])
        by_id = {r.line_id: r.cdt_code for r in out.recommendations}
        self.assertEqual(by_id["1"], "D0120")
        self.assertEqual(by_id["2"], "D9944")


class TestSample02bSkipsLlm(unittest.TestCase):
    def test_fourteen_line_fixture_does_not_call_llm(self) -> None:
        req = CodingSuggestRequest.model_validate(
            json.loads(SAMPLE_02B.read_text(encoding="utf-8"))
        )
        mock_llm = MagicMock(side_effect=AssertionError("LLM should not be called"))
        with (
            patch("app.coding.service.llm_generate_line_recommendations", mock_llm),
            patch("app.coding.service.llm_generate_codes", mock_llm),
            patch("app.coding.service.fetch_run_by_request_id", return_value=None),
            patch("app.coding.service.insert_coding_run", return_value=None),
            patch("app.coding.service.write_audit_log"),
            patch("app.coding.service.create_supabase", return_value=None),
        ):
            out = run_coding_suggest(
                req,
                settings=Settings(openrouter_api_key="x"),
                coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
            )
        mock_llm.assert_not_called()
        by_id = {r.line_id: r.cdt_code for r in out.recommendations}
        self.assertIsNone(by_id["A"])
        self.assertEqual(by_id["B"], "D0210")
        self.assertEqual(by_id["C"], "D0180")
        self.assertIsNone(by_id["D"])
        self.assertEqual(by_id["E"], "D1330")
        self.assertIsNone(by_id["F"])
        self.assertEqual(by_id["G"], "D2740")
        self.assertEqual(by_id["H"], "D2740")
        self.assertEqual(by_id["I"], "D4341")
        self.assertEqual(by_id["J"], "D4341")
        self.assertEqual(by_id["K"], "D4341")
        self.assertEqual(by_id["L"], "D4341")
        self.assertIsNone(by_id["M"])
        self.assertEqual(by_id["N"], "D4921")


if __name__ == "__main__":
    unittest.main()
