"""Claim agent + full RCM pipeline (LLMs mocked where needed)."""

import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.claim_agent import run_claim_agent, submit_reviewed_claim
from app.agents.rcm_pipeline import run_full_rcm_pipeline
from app.config import Settings
from app.schemas.claim import ClaimAgentRequest, FullRcmPipelineRequest, PatientInfo, ProviderInfo
from app.schemas.coding import CodingAgentResponse
from app.schemas.prior_auth import PriorAuthAgentResponse

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "full_rcm_pipeline_request.json"


class TestClaimAgent(unittest.TestCase):
    @staticmethod
    def _billing_payload() -> dict:
        return {
            "claim_frequency_code": "1",
            "place_of_service": "11",
            "patient_account_number": "ACCT-1001",
            "patient_sex": "F",
            "patient_address": {
                "line1": "101 Main St",
                "city": "Albany",
                "state": "NY",
                "postal_code": "12207",
            },
            "subscriber": {
                "member_id": "MEM12345",
                "relationship_to_patient": "self",
                "name": "Jane Doe",
                "dob": "1987-03-15",
                "address": {
                    "line1": "101 Main St",
                    "city": "Albany",
                    "state": "NY",
                    "postal_code": "12207",
                },
            },
            "billing_provider": {
                "name": "Capital Dental Group",
                "npi": "1234567890",
                "tax_id": "123456789",
                "taxonomy_code": "1223G0001X",
                "address": {
                    "line1": "200 Clinic Rd",
                    "city": "Albany",
                    "state": "NY",
                    "postal_code": "12208",
                },
            },
            "rendering_provider": {
                "name": "Dr X",
                "npi": "1234567890",
                "taxonomy_code": "1223G0001X",
            },
            "payer": {
                "payer_name": "Delta Dental PPO",
                "payer_id": "DDPPO01",
                "plan_name": "PPO Plus",
            },
            "diagnosis_codes": ["K02.9"],
            "service_lines": [
                {
                    "line_number": 1,
                    "service_date": "2026-01-20",
                    "cdt_code": "D1110",
                    "units": Decimal("1"),
                    "charge_amount": Decimal("125.00"),
                    "diagnosis_pointers": [1],
                    "tooth_number": None,
                    "surface": None,
                    "prior_auth_number": None,
                }
            ],
            "total_charge_amount": Decimal("125.00"),
        }

    def test_fixture_loads(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        req = FullRcmPipelineRequest.model_validate(data)
        self.assertEqual(req.patient.name, "Jane Doe")

    def test_full_pipeline_accepts_encounter_id_without_direct_billing(self) -> None:
        req = FullRcmPipelineRequest.model_validate(
            {
                "clinical_note": "Prophy visit",
                "patient_age": 38,
                "insurance": "Delta Dental PPO",
                "encounter_id": "enc-123",
            }
        )
        self.assertEqual(req.encounter_id, "enc-123")
        self.assertIsNone(req.billing)

    def test_submit_when_prior_clear(self) -> None:
        coding = CodingAgentResponse(
            cdt_codes=["D1110"],
            icd10_codes=["K02.9"],
            confidence=0.9,
            justification="Prophy",
            payer_flags=[],
        )
        prior = PriorAuthAgentResponse(
            requires_auth=False,
            required_documents=[],
            payer_rules=[],
            risk_level="low",
            risk_reason="",
        )
        req = ClaimAgentRequest(
            coding=coding,
            prior_auth=prior,
            patient=PatientInfo(name="A", dob="2000-01-01"),
            provider=ProviderInfo(name="Dr X", npi="1234567890"),
            billing=self._billing_payload(),
        )
        out = run_claim_agent(req)
        self.assertEqual(out.status, "submitted")
        self.assertTrue(out.claim_id.startswith("CLM"))
        self.assertEqual(out.submission_channel, "stedi_mock")
        self.assertEqual(out.details.get("cdt_codes"), ["D1110"])

    def test_pending_auth_when_requires_auth(self) -> None:
        coding = CodingAgentResponse(
            cdt_codes=["D2740"],
            icd10_codes=["K02.9"],
            confidence=0.8,
            justification="Crown",
            payer_flags=[],
        )
        prior = PriorAuthAgentResponse(
            requires_auth=True,
            required_documents=[],
            payer_rules=[],
            risk_level="high",
            risk_reason="",
        )
        req = ClaimAgentRequest(
            coding=coding,
            prior_auth=prior,
            patient=PatientInfo(name="A", dob="2000-01-01"),
            provider=ProviderInfo(name="Dr X", npi="1234567890"),
            billing=self._billing_payload(),
        )
        out = run_claim_agent(req)
        self.assertEqual(out.status, "pending_auth")
        self.assertEqual(out.claim_id, "")
        self.assertEqual(out.submission_channel, "none")

    def test_pending_auth_when_documents_required(self) -> None:
        prior = PriorAuthAgentResponse(
            requires_auth=False,
            required_documents=["Panoramic X-ray"],
            payer_rules=[],
            risk_level="medium",
            risk_reason="",
        )
        coding = CodingAgentResponse(
            cdt_codes=["D0120"],
            icd10_codes=[],
            confidence=0.9,
            justification="Exam",
            payer_flags=[],
        )
        req = ClaimAgentRequest(
            coding=coding,
            prior_auth=prior,
            patient=PatientInfo(name="A", dob="2000-01-01"),
            provider=ProviderInfo(name="Dr X", npi="1234567890"),
            billing=self._billing_payload(),
        )
        out = run_claim_agent(req)
        self.assertEqual(out.status, "pending_auth")

    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_full_pipeline_mocked(self, mock_coding: MagicMock, mock_pa: MagicMock) -> None:
        mock_coding.return_value = {
            "cdt_codes": ["D1110"],
            "icd10_codes": ["K02.9"],
            "confidence": 0.9,
            "justification": "Prophy",
        }
        mock_pa.return_value = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": [],
            "risk_level": "low",
            "risk_reason": "ok",
        }
        settings = Settings(openrouter_api_key="x")
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        full = FullRcmPipelineRequest.model_validate(data)
        out = run_full_rcm_pipeline(settings, None, full)
        self.assertEqual(out.claim_draft.status, "draft")
        self.assertEqual(out.claim_draft.available_actions, ["edit", "submit"])
        self.assertTrue(bool(out.claim_draft.claim_payload))
        self.assertIn("D1110", out.coding.cdt_codes)

    @patch("app.agents.rcm_pipeline._fetch_claim_snapshot")
    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_full_pipeline_uses_snapshot_context(
        self,
        mock_coding: MagicMock,
        mock_pa: MagicMock,
        mock_snapshot: MagicMock,
    ) -> None:
        mock_coding.return_value = {
            "cdt_codes": ["D1110"],
            "icd10_codes": ["K02.9"],
            "confidence": 0.9,
            "justification": "Prophy",
        }
        mock_pa.return_value = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": [],
            "risk_level": "low",
            "risk_reason": "ok",
        }
        mock_snapshot.return_value = {
            "ready_for_claim": True,
            "patient": {
                "name": "Jane Doe",
                "dob": "1987-03-15",
                "address": {
                    "line1": "101 Main St",
                    "city": "Albany",
                    "state": "NY",
                    "postal_code": "12207",
                },
            },
            "subscriber": {
                "member_id": "MEM12345",
                "relationship_to_patient": "self",
                "name": "Jane Doe",
                "dob": "1987-03-15",
                "address": {
                    "line1": "101 Main St",
                    "city": "Albany",
                    "state": "NY",
                    "postal_code": "12207",
                },
            },
            "payer": {
                "payer_name": "Delta Dental PPO",
                "payer_id": "DDPPO01",
                "plan_name": "PPO Plus",
            },
            "billing_provider": {
                "name": "Capital Dental Group",
                "npi": "1234567890",
                "tax_id": "123456789",
                "taxonomy_code": "1223G0001X",
                "address": {
                    "line1": "200 Clinic Rd",
                    "city": "Albany",
                    "state": "NY",
                    "postal_code": "12208",
                },
            },
            "rendering_provider": {
                "name": "Dr X",
                "npi": "1234567890",
                "taxonomy_code": "1223G0001X",
            },
            "claim_header": {
                "claim_frequency_code": "1",
                "place_of_service": "11",
                "patient_account_number": "ACCT-1001",
                "patient_sex": "F",
            },
            "diagnosis_codes": ["K02.9"],
            "service_lines": [
                {
                    "line_number": 1,
                    "service_date": "2026-01-20",
                    "cdt_code": "D1110",
                    "units": "1",
                    "charge_amount": "125.00",
                    "diagnosis_pointers": [1],
                    "tooth_number": None,
                    "surface": None,
                    "prior_auth_number": None,
                }
            ],
            "financials": {"total_charge_amount": "125.00"},
        }
        settings = Settings(openrouter_api_key="x")
        request = FullRcmPipelineRequest.model_validate(
            {
                "clinical_note": "Adult prophylaxis only",
                "patient_age": 38,
                "insurance": "Delta Dental PPO",
                "encounter_id": "enc-123",
                "mock_era": {"status": "paid", "reason": ""},
            }
        )
        out = run_full_rcm_pipeline(settings, MagicMock(), request)
        self.assertEqual(out.claim_draft.status, "draft")
        self.assertEqual(out.claim_draft.available_actions, ["edit", "submit"])

    def test_submit_reviewed_claim_from_draft_payload(self) -> None:
        coding = CodingAgentResponse(
            cdt_codes=["D1110"],
            icd10_codes=["K02.9"],
            confidence=0.9,
            justification="Prophy",
            payer_flags=[],
        )
        prior = PriorAuthAgentResponse(
            requires_auth=False,
            required_documents=[],
            payer_rules=[],
            risk_level="low",
            risk_reason="",
        )
        req = ClaimAgentRequest(
            coding=coding,
            prior_auth=prior,
            patient=PatientInfo(name="A", dob="2000-01-01"),
            provider=ProviderInfo(name="Dr X", npi="1234567890"),
            billing=self._billing_payload(),
        )
        # submit_reviewed_claim expects the full claim payload from a reviewed draft.
        from app.tools.claim_tools import build_claim_tool

        submit = submit_reviewed_claim(build_claim_tool(req))
        self.assertEqual(submit.status, "submitted")
        self.assertTrue(submit.claim_id.startswith("CLM"))


if __name__ == "__main__":
    unittest.main()
