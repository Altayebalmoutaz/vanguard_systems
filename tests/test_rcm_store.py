"""Tests for Wave 6 RCM BFF store shapers."""

from __future__ import annotations

import unittest

from app.dashboard.rcm_store import (
    _shape_claim_row,
    _shape_coding_decision,
    _shape_denial_row,
    _shape_prior_auth_run,
)


class RcmStoreShaperTests(unittest.TestCase):
    def test_shape_coding_decision(self) -> None:
        row = {
            "id": "dec-1",
            "encounter_id": "enc-1",
            "patient_name": "Jane Doe",
            "dob": "1990-01-15",
            "provider_name": "Dr Smith",
            "payer": "Delta",
            "clinical_note": "Exam",
            "reasoning": "Routine prophy",
            "confidence": 0.72,
            "status": "pending_review",
            "created_at": "2026-07-04T10:00:00+00:00",
            "output": {
                "cdt_codes": ["D1110"],
                "icd10_codes": ["K05.10"],
                "payer_flags": [],
                "payer_rules_matched": [],
            },
        }
        shaped = _shape_coding_decision(row)
        self.assertEqual(shaped["id"], "dec-1")
        self.assertEqual(shaped["cdt_codes"], ["D1110"])
        self.assertEqual(shaped["confidence"], 0.72)

    def test_shape_claim_row(self) -> None:
        row = {
            "id": "claim-1",
            "patient_name": "Jane Doe",
            "dob": "1990-01-15",
            "payer": "Delta",
            "provider": "Dr Smith",
            "status": "draft",
            "icd10_codes": ["K05.10"],
            "cdt_lines": {
                "service_lines": [{"cdt_code": "D1110", "charge_amount": 120}],
            },
            "compliance_flags": [],
            "created_at": "2026-07-04T10:00:00+00:00",
        }
        shaped = _shape_claim_row(row)
        self.assertEqual(shaped["claim_id"], "claim-1")
        self.assertEqual(shaped["total_charge_amount"], 120.0)

    def test_shape_prior_auth_run(self) -> None:
        row = {
            "id": "run-1",
            "patient_name": "Jane Doe",
            "dob": "1990-01-15",
            "payer_id": "84103",
            "status": "pending_review",
            "created_at": "2026-07-04T10:00:00+00:00",
            "input_json": {"coding": {"cdt_codes": ["D2740"]}, "insurance": "Delta"},
            "output_json": {
                "requires_auth": True,
                "required_documents": ["xray"],
                "payer_rules": ["Crown rule"],
                "risk_level": "high",
                "risk_reason": "High-value crown",
            },
        }
        shaped = _shape_prior_auth_run(row)
        self.assertEqual(shaped["procedure"], "D2740")
        self.assertTrue(shaped["requires_auth"])
        self.assertEqual(shaped["risk_level"], "high")

    def test_shape_denial_row(self) -> None:
        row = {
            "id": "den-1",
            "claim_reference": "CLM123",
            "patient_name": "Jane Doe",
            "payer": "Delta",
            "provider_code": "missing_info",
            "root_cause": "Missing documentation",
            "corrective_actions": "Submit xray\nAppeal if needed",
            "recoverable_amount": "$450",
            "executive_summary": "Missing periapical",
            "status": "pending",
            "created_at": "2026-07-04T10:00:00+00:00",
        }
        shaped = _shape_denial_row(row)
        self.assertEqual(shaped["claim_id"], "CLM123")
        self.assertEqual(shaped["amount_at_risk"], 450.0)
        self.assertEqual(len(shaped["resubmission_steps"]), 2)


if __name__ == "__main__":
    unittest.main()
