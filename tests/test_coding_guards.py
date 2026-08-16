"""Unit tests for deterministic coding-agent clinical guards."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from app.coding.gaps import post_check_line, pre_check_line
from app.coding.guards import apply_clinical_guards, planned_crown_material_documented
from app.coding.schemas import (
    CodingSuggestRequest,
    MissingInfoCode,
    PatientInfo,
    ProcedureLine,
    resolved_quadrant,
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


def _request(procedures: list[ProcedureLine]) -> CodingSuggestRequest:
    return CodingSuggestRequest(
        request_id=uuid4(),
        practice_id="vgd_mock_brooklyn",
        patient_id="pat_1",
        provider_id="prov_1",
        encounter_datetime=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        patient=PatientInfo(age=62),
        procedures=procedures,
    )


class TestRestorativePreCheckFalsePositives(unittest.TestCase):
    def test_buccal_mucosa_on_exam_is_not_restorative(self) -> None:
        line = _line(
            line_id="C1",
            findings=[
                "Comprehensive examination performed by the doctor",
                "Sloughing of the buccal mucosa noted",
            ],
        )
        codes = {m.code for m in pre_check_line(line)}
        self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_furcation_buccal_on_perio_eval_is_not_restorative(self) -> None:
        line = _line(
            line_id="C3",
            findings=[
                "Periodontal evaluation with periodontal charting",
                "Furcation involvement: #3 buccal; #15 Class II buccal",
            ],
        )
        codes = {m.code for m in pre_check_line(line)}
        self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_crown_with_existing_amalgam_narrative_does_not_need_surface(self) -> None:
        line = _line(
            line_id="P2",
            tooth_numbers=["30"],
            findings=[
                "Full porcelain crown",
                "Existing MOD amalgam at #30",
                "Lingual cusp compromised showing signs of wear",
            ],
            planned_or_performed="planned",
        )
        codes = {m.code for m in pre_check_line(line)}
        self.assertNotIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertNotIn(MissingInfoCode.SURFACE_MISSING, codes)

    def test_filling_still_requires_tooth_and_surface(self) -> None:
        line = _line(findings=["interproximal caries"])
        codes = {m.code for m in pre_check_line(line)}
        self.assertIn(MissingInfoCode.TOOTH_MISSING, codes)
        self.assertIn(MissingInfoCode.SURFACE_MISSING, codes)


class TestRadiographAttachmentAliases(unittest.TestCase):
    def test_full_mouth_series_satisfies_radiograph_gap(self) -> None:
        line = _line(line_id="C2")
        missing = post_check_line(
            line,
            cdt_code="D0210",
            attachments_present=["full_mouth_series"],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={"requires_radiograph": True},
        )
        self.assertFalse(any(m.code == MissingInfoCode.RADIOGRAPH_MISSING for m in missing))

    def test_fmx_alias_satisfies_radiograph_gap(self) -> None:
        line = _line(line_id="C2")
        missing = post_check_line(
            line,
            cdt_code="D0210",
            attachments_present=["FMX"],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={"requires_radiograph": True},
        )
        self.assertFalse(any(m.code == MissingInfoCode.RADIOGRAPH_MISSING for m in missing))

    def test_srp_with_quadrants_does_not_require_tooth(self) -> None:
        line = _line(line_id="1", findings=["scaling and root planing, 4 quadrants"])
        missing = post_check_line(
            line,
            cdt_code="D4341",
            attachments_present=["full_mouth_series"],
            confidence=0.97,
            threshold=0.75,
            cdt_meta={"requires_radiograph": True, "requires_tooth": True},
        )
        self.assertFalse(any(m.code == MissingInfoCode.TOOTH_MISSING for m in missing))

    def test_periodontal_chart_is_not_a_radiograph(self) -> None:
        line = _line(line_id="P3")
        missing = post_check_line(
            line,
            cdt_code="D4341",
            attachments_present=["periodontal_chart"],
            confidence=0.9,
            threshold=0.75,
            cdt_meta={"requires_radiograph": True, "requires_tooth": False},
        )
        self.assertTrue(any(m.code == MissingInfoCode.RADIOGRAPH_MISSING for m in missing))


class TestClinicalGuards(unittest.TestCase):
    def test_keeps_crown_when_replacement_material_missing(self) -> None:
        line = _line(
            line_id="P1",
            tooth_numbers=["15"],
            surfaces=["M", "B"],
            findings=[
                "Replacement of existing full gold crown",
                "Existing full gold crown present at #15",
                "Recurrent decay noted along the mesiobuccal margin",
            ],
            planned_or_performed="planned",
        )
        self.assertFalse(planned_crown_material_documented(line))
        recs = [
            {
                "line_id": "P1",
                "cdt_code": "D2740",
                "confidence": 0.85,
                "explanation": "crown",
                "icd10_codes": [],
            }
        ]
        warnings = apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D2740")
        self.assertLessEqual(recs[0]["confidence"], 0.7)
        self.assertTrue(any("crown material" in w for w in warnings))

    def test_does_not_default_recement_or_temp_or_implant_to_d2740(self) -> None:
        recement = _line(line_id="1", tooth_numbers=["7"], findings=["recement crown"])
        temp = _line(line_id="2", tooth_numbers=["30"], findings=["temporary crown placement"])
        implant = _line(
            line_id="3",
            tooth_numbers=["19"],
            findings=["implant-supported porcelain crown delivery"],
        )
        recs = [
            {"line_id": "1", "cdt_code": None, "confidence": 0.0, "explanation": ""},
            {"line_id": "2", "cdt_code": None, "confidence": 0.0, "explanation": ""},
            {"line_id": "3", "cdt_code": None, "confidence": 0.0, "explanation": ""},
        ]
        apply_clinical_guards(_request([recement, temp, implant]), recs)
        self.assertIsNone(recs[0]["cdt_code"])
        self.assertIsNone(recs[1]["cdt_code"])
        self.assertIsNone(recs[2]["cdt_code"])

    def test_defaults_null_crown_to_d2740(self) -> None:
        line = _line(
            line_id="G",
            tooth_numbers=["15"],
            findings=["Replacement of existing full gold crown"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "G",
                "cdt_code": None,
                "confidence": 0.0,
                "explanation": "",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D2740")
        self.assertLessEqual(recs[0]["confidence"], 0.7)

    def test_keeps_porcelain_crown_when_material_is_stated(self) -> None:
        line = _line(
            line_id="P2",
            tooth_numbers=["30"],
            findings=["Full porcelain crown"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P2",
                "cdt_code": "D2740",
                "confidence": 0.85,
                "explanation": "porcelain crown",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D2740")

    def test_voids_d4346_used_as_laser_adjunct_with_srp(self) -> None:
        srp = _line(
            line_id="P3-UR",
            findings=["quadrant: UR", "Non-surgical periodontal therapy", "bone loss"],
            planned_or_performed="planned",
        )
        laser = _line(
            line_id="P4",
            findings=["Laser-assisted periodontal therapy"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P3-UR",
                "cdt_code": "D4341",
                "confidence": 0.9,
                "explanation": "srp",
                "icd10_codes": [],
            },
            {
                "line_id": "P4",
                "cdt_code": "D4346",
                "confidence": 0.8,
                "explanation": "laser",
                "icd10_codes": [],
            },
        ]
        apply_clinical_guards(_request([srp, laser]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4341")
        self.assertIsNone(recs[1]["cdt_code"])

    def test_rewrites_irrigation_d4999_to_d4921(self) -> None:
        line = _line(
            line_id="P5",
            findings=["Gingival irrigation"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P5",
                "cdt_code": "D4999",
                "confidence": 0.7,
                "explanation": "unspecified",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4921")

    def test_d0150_and_d0180_keep_periodontal_eval(self) -> None:
        exam = _line(
            line_id="C1",
            findings=["Comprehensive examination", "New patient"],
        )
        perio = _line(
            line_id="C3",
            findings=["Periodontal evaluation with periodontal charting"],
        )
        recs = [
            {
                "line_id": "C1",
                "cdt_code": "D0150",
                "confidence": 0.9,
                "explanation": "exam",
                "icd10_codes": [],
            },
            {
                "line_id": "C3",
                "cdt_code": "D0180",
                "confidence": 0.9,
                "explanation": "perio eval",
                "icd10_codes": [],
            },
        ]
        apply_clinical_guards(_request([exam, perio]), recs)
        self.assertIsNone(recs[0]["cdt_code"])
        self.assertEqual(recs[1]["cdt_code"], "D0180")

    def test_true_gingivitis_d4346_is_left_alone(self) -> None:
        line = _line(
            line_id="1",
            findings=["Generalized moderate gingival inflammation without bone loss"],
        )
        recs = [
            {
                "line_id": "1",
                "cdt_code": "D4346",
                "confidence": 0.9,
                "explanation": "gingivitis scaling",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4346")

    def test_resolved_quadrant_from_findings_token(self) -> None:
        line = _line(findings=["quadrant: UR (upper right)", "Non-surgical periodontal therapy"])
        self.assertEqual(resolved_quadrant(line).value, "UR")

    def test_srp_tooth_count_rewrites_d4341_to_d4342(self) -> None:
        line = _line(
            line_id="P3-UR",
            tooth_numbers=["2", "3"],
            quadrant="UR",
            findings=["Non-surgical periodontal therapy"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P3-UR",
                "cdt_code": "D4341",
                "confidence": 0.9,
                "explanation": "srp",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4342")

    def test_srp_tooth_count_rewrites_d4342_to_d4341(self) -> None:
        line = _line(
            line_id="P3-UR",
            tooth_numbers=["2", "3", "4", "5"],
            quadrant="UR",
            findings=["Non-surgical periodontal therapy"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P3-UR",
                "cdt_code": "D4342",
                "confidence": 0.9,
                "explanation": "srp",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4341")

    def test_quadrant_only_srp_defaults_d4342_to_d4341(self) -> None:
        line = _line(
            line_id="P3-UR",
            quadrant="UR",
            findings=["Non-surgical periodontal therapy"],
            planned_or_performed="planned",
        )
        recs = [
            {
                "line_id": "P3-UR",
                "cdt_code": "D4342",
                "confidence": 0.9,
                "explanation": "srp",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertEqual(recs[0]["cdt_code"], "D4341")

    def test_voids_filling_when_surface_count_does_not_match(self) -> None:
        line = _line(
            line_id="1",
            tooth_numbers=["14"],
            surfaces=["O"],
            findings=["odd chairside repair"],
        )
        recs = [
            {
                "line_id": "1",
                "cdt_code": "D2393",
                "confidence": 0.8,
                "explanation": "three-surface composite",
                "icd10_codes": [],
            }
        ]
        apply_clinical_guards(_request([line]), recs)
        self.assertIsNone(recs[0]["cdt_code"])


if __name__ == "__main__":
    unittest.main()
