"""Denial / ERA agent and tool mapping tests."""

import unittest
from unittest.mock import patch

from app.agents.denial_agent import run_denial_agent
from app.schemas.denial import DenialAgentRequest, MockEraResponse
from app.tools.denial_tools import (
    auto_resubmit_tool,
    map_denial_reason_tool,
    parse_era_tool,
    suggest_action_tool,
)


class TestDenialTools(unittest.TestCase):
    def test_parse_era_paid(self) -> None:
        p = parse_era_tool({"status": "paid"})
        self.assertEqual(p["status"], "paid")
        self.assertEqual(p["reason"], "")

    def test_parse_era_denied(self) -> None:
        p = parse_era_tool({"status": "denied", "reason": "missing_xray"})
        self.assertEqual(p["status"], "denied")
        self.assertEqual(p["reason"], "missing_xray")

    def test_parse_era_nested_mock_era(self) -> None:
        p = parse_era_tool({"claim_id": "X", "mock_era": {"status": "partial", "reason": "frequency_limit"}})
        self.assertEqual(p["status"], "partial")
        self.assertEqual(p["reason"], "frequency_limit")

    def test_map_denial_reason_spec(self) -> None:
        self.assertEqual(map_denial_reason_tool("paid", ""), "none")
        self.assertEqual(map_denial_reason_tool("denied", "missing_xray"), "upload_xray_and_resubmit")
        self.assertEqual(map_denial_reason_tool("denied", "invalid_code"), "correct_code_and_resubmit")
        self.assertEqual(map_denial_reason_tool("denied", "not_covered"), "notify_patient")
        self.assertEqual(
            map_denial_reason_tool("partial", "frequency_limit"),
            "review_contract_and_patient_balance",
        )

    def test_suggest_action_alias_matches_map(self) -> None:
        self.assertEqual(
            suggest_action_tool("denied", "missing_xray"),
            map_denial_reason_tool("denied", "missing_xray"),
        )

    def test_auto_resubmit_steps_nonempty(self) -> None:
        steps = auto_resubmit_tool("CLM1", "upload_xray_and_resubmit")
        self.assertTrue(len(steps) >= 2)
        self.assertIn("CLM1", steps[0])


class TestDenialAgent(unittest.TestCase):
    def test_paid_flow(self) -> None:
        req = DenialAgentRequest(
            claim_id="CLM12345",
            cdt_codes=["D1110"],
            icd10_codes=["K02.9"],
            patient_name="Jane",
            mock_era=MockEraResponse(status="paid"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.claim_id, "CLM12345")
        self.assertEqual(out.status, "paid")
        self.assertEqual(out.reason, "")
        self.assertEqual(out.next_action, "none")
        self.assertEqual(out.appeal_letter, "")
        self.assertEqual(out.resubmission_steps, [])

    def test_denied_missing_xray_appeal_and_steps(self) -> None:
        req = DenialAgentRequest(
            claim_id="CLM99999",
            cdt_codes=["D2740"],
            icd10_codes=["K02.9"],
            patient_name="Jane Doe",
            insurance_company_name="Delta Dental",
            provider_name="Dr. Smith DDS",
            mock_era=MockEraResponse(status="denied", reason="missing_xray"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.status, "denied")
        self.assertEqual(out.reason, "missing_xray")
        self.assertEqual(out.next_action, "upload_xray_and_resubmit")
        self.assertIn("Appeal for Claim ID CLM99999", out.appeal_letter)
        self.assertIn("Jane Doe", out.appeal_letter)
        self.assertIn("Delta Dental", out.appeal_letter)
        self.assertIn("Dr. Smith DDS", out.appeal_letter)
        self.assertIn("D2740", out.appeal_letter)
        self.assertTrue(len(out.resubmission_steps) > 0)

    def test_denied_not_covered(self) -> None:
        req = DenialAgentRequest(
            claim_id="CLM888",
            patient_name="Pat",
            mock_era=MockEraResponse(status="denied", reason="not_covered"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.next_action, "notify_patient")
        self.assertIn("Re: Appeal for Claim ID CLM888", out.appeal_letter)

    def test_partial_frequency(self) -> None:
        req = DenialAgentRequest(
            claim_id="CLM11111",
            mock_era=MockEraResponse(status="partial", reason="frequency_limit"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.status, "partial")
        self.assertEqual(out.reason, "frequency_limit")
        self.assertEqual(out.next_action, "review_contract_and_patient_balance")
        self.assertEqual(out.appeal_letter, "")

    def test_empty_claim_id_not_submitted(self) -> None:
        req = DenialAgentRequest(claim_id="", mock_era=MockEraResponse(status="paid"))
        out = run_denial_agent(req)
        self.assertEqual(out.status, "denied")
        self.assertEqual(out.reason, "claim_not_submitted")
        self.assertEqual(out.appeal_letter, "")
        self.assertTrue(out.resubmission_steps)

    @patch("app.agents.denial_agent.denial_llm_intelligence_tool")
    def test_llm_mismatch_flags_human_review(self, mock_llm) -> None:
        mock_llm.return_value = {
            "reason_token": "invalid_code",
            "suggested_next_action": "correct_code_and_resubmit",
            "required_evidence": ["EOB line details"],
            "confidence": 0.91,
            "reasoning_summary": "Payer edits suggest coding correction.",
        }
        req = DenialAgentRequest(
            claim_id="CLM123",
            mock_era=MockEraResponse(status="denied", reason="missing_xray"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.deterministic_reason_token, "missing_xray")
        self.assertEqual(out.llm_reason_token, "invalid_code")
        self.assertTrue(out.requires_human_review)
        self.assertGreater(out.llm_confidence, 0.9)

    @patch("app.agents.denial_agent.denial_llm_intelligence_tool")
    def test_llm_failure_keeps_deterministic_output(self, mock_llm) -> None:
        mock_llm.side_effect = RuntimeError("llm unavailable")
        req = DenialAgentRequest(
            claim_id="CLM777",
            mock_era=MockEraResponse(status="denied", reason="not_covered"),
        )
        out = run_denial_agent(req)
        self.assertEqual(out.next_action, "notify_patient")
        self.assertEqual(out.llm_reason_token, "")
        self.assertFalse(out.requires_human_review)


if __name__ == "__main__":
    unittest.main()
