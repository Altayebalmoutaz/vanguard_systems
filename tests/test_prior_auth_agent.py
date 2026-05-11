"""
Prior auth agent tests (LLM mocked).
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.prior_auth_agent import run_prior_auth_agent
from app.config import Settings
from app.schemas.coding import CodingAgentResponse
from app.schemas.prior_auth import PriorAuthAgentRequest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "prior_auth_example_request.json"


class TestPriorAuthAgent(unittest.TestCase):
    def test_fixture_loads(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        req = PriorAuthAgentRequest.model_validate(data)
        self.assertIn("D2740", req.coding.cdt_codes)

    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    def test_crown_triggers_auth_from_rules(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": ["LLM: optional note"],
            "risk_level": "low",
            "risk_reason": "LLM low risk",
        }
        settings = Settings(openrouter_api_key="x")
        coding = CodingAgentResponse(
            cdt_codes=["D2740"],
            icd10_codes=["K02.9"],
            confidence=0.9,
            justification="Crown",
            payer_flags=[],
        )
        req = PriorAuthAgentRequest(
            coding=coding,
            insurance="Delta Dental PPO",
            clinical_note=None,
        )
        out = run_prior_auth_agent(settings, req)
        self.assertTrue(out.requires_auth)
        self.assertEqual(out.status, "pending_review")
        self.assertTrue(any("D2740" in r for r in out.payer_rules))
        self.assertTrue(len(out.required_documents) > 0)

    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    def test_llm_failure_uses_fallback(self, mock_llm: MagicMock) -> None:
        mock_llm.side_effect = RuntimeError("OpenRouter down")
        settings = Settings(openrouter_api_key="x")
        coding = CodingAgentResponse(
            cdt_codes=["D1110"],
            icd10_codes=[],
            confidence=0.9,
            justification="Prophy",
            payer_flags=[],
        )
        req = PriorAuthAgentRequest(coding=coding, insurance="Aetna")
        out = run_prior_auth_agent(settings, req)
        self.assertFalse(out.requires_auth)
        self.assertTrue(any("LLM unavailable" in r for r in out.payer_rules))

    @patch("app.integrations.agent_runs.insert_agent_run")
    @patch("app.tools.prior_auth_db.fetch_deterministic_prior_auth_from_supabase")
    @patch("app.integrations.supabase_client.get_supabase_client")
    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    def test_supabase_prior_auth_merged_with_stub(
        self,
        mock_llm: MagicMock,
        mock_get_sb: MagicMock,
        mock_fetch: MagicMock,
        mock_insert_run: MagicMock,
    ) -> None:
        mock_get_sb.return_value = MagicMock()
        mock_fetch.return_value = {
            "requires_auth": True,
            "required_documents": ["Payer-specific radiograph"],
            "payer_rules": ["[DB] Medicaid prior auth applies"],
        }
        mock_llm.return_value = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": [],
            "risk_level": "low",
            "risk_reason": "",
        }
        settings = Settings(openrouter_api_key="x")
        coding = CodingAgentResponse(
            cdt_codes=["D1110"],
            icd10_codes=[],
            confidence=0.9,
            justification="Prophy",
            payer_flags=[],
        )
        req = PriorAuthAgentRequest(
            coding=coding,
            insurance="Aetna",
            patient_age=42,
        )
        out = run_prior_auth_agent(settings, req)
        self.assertTrue(out.requires_auth)
        self.assertTrue(any("Payer-specific" in d for d in out.required_documents))
        self.assertTrue(any("[DB]" in r for r in out.payer_rules))
        mock_fetch.assert_called_once()
        mock_insert_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
