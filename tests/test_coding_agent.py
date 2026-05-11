"""
Unit tests for the Dental Coding Agent (LLM mocked — no API keys required).
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.coding_agent import run_coding_agent
from app.config import Settings
from app.schemas.coding import CodingAgentRequest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "coding_example_request.json"


class TestCodingAgent(unittest.TestCase):
    def test_example_fixture_loads(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        req = CodingAgentRequest.model_validate(data)
        self.assertEqual(req.insurance, "Delta Dental PPO")

    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_agent_loop_happy_path(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = {
            "cdt_codes": ["D0120", "D0150"],
            "icd10_codes": ["K02.9"],
            "confidence": 0.88,
            "justification": "Periodic and comprehensive evaluations documented.",
        }
        settings = Settings(openrouter_api_key="test-key")
        req = CodingAgentRequest(
            clinical_note="Test note",
            patient_age=40,
            insurance="Delta Dental PPO",
        )
        out = run_coding_agent(settings, None, req)
        self.assertEqual(out.status, "pending_review")
        self.assertEqual(out.cdt_codes, ["D0120", "D0150"])
        self.assertEqual(out.icd10_codes, ["K02.9"])
        self.assertGreaterEqual(out.confidence, 0.0)
        # Payer rules load from payer_rules; without Supabase they are skipped.
        self.assertTrue(
            any("payer rules" in f.lower() for f in out.payer_flags),
            msg=out.payer_flags,
        )
        mock_llm.assert_called_once()

    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_icd_validation_flags_unknown_code(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = {
            "cdt_codes": ["D0120"],
            "icd10_codes": ["Z99.99"],
            "confidence": 0.5,
            "justification": "Uncertain mapping.",
        }
        settings = Settings(openrouter_api_key="test-key")
        req = CodingAgentRequest(
            clinical_note="Vague note",
            patient_age=30,
            insurance="Other Payer",
        )
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[]
        )
        out = run_coding_agent(settings, mock_sb, req)
        self.assertEqual(out.status, "pending_review")
        self.assertTrue(any("icd10_dental_gem_axis" in f for f in out.payer_flags))
        self.assertTrue(any("confidence" in f.lower() for f in out.payer_flags))

    @patch("app.agents.coding_agent.validate_cdt_tool")
    @patch("app.agents.coding_agent.validate_icd_tool")
    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_payer_rules_from_coding_agent_table(self, mock_llm: MagicMock, mock_icd: MagicMock, mock_cdt: MagicMock) -> None:
        mock_llm.return_value = {
            "cdt_codes": ["D0120", "D0150"],
            "icd10_codes": ["K02.9"],
            "confidence": 0.9,
            "justification": "Evaluations documented.",
        }
        mock_icd.return_value = {"invalid": [], "verified": ["K02.9"], "icd_flags": []}
        mock_cdt.return_value = {
            "invalid": [],
            "verified": ["D0120", "D0150"],
            "cdt_flags": [],
            "reference_size": 500,
        }
        pr_or = MagicMock()
        pr_or.execute.return_value = MagicMock(
            data=[
                {
                    "id": 1,
                    "payer_name": "Delta Dental",
                    "rule_type": "bundling_review",
                    "code": None,
                    "rule_text": "Review D0120 vs D0150 same DOS.",
                    "related_codes": None,
                    "conditions": {"require_all_codes": ["D0120", "D0150"]},
                }
            ]
        )
        pr_select = MagicMock()
        pr_select.or_.return_value = pr_or
        pr_table = MagicMock()
        pr_table.select.return_value = pr_select
        mock_sb = MagicMock()
        mock_sb.table.return_value = pr_table

        settings = Settings(openrouter_api_key="test-key")
        req = CodingAgentRequest(
            clinical_note="Periodic and comp eval.",
            patient_age=40,
            insurance="Delta Dental PPO",
        )
        out = run_coding_agent(settings, mock_sb, req)
        mock_sb.table.assert_any_call("payer_rules")
        self.assertEqual(len(out.payer_rules_matched), 1)
        self.assertTrue(any("bundling_review" in f for f in out.payer_flags))
        self.assertTrue(any("payer_rules" in f for f in out.payer_flags))


if __name__ == "__main__":
    unittest.main()
