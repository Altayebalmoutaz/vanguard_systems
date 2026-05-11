"""RCM pipeline: coding → prior auth (coding LLM mocked)."""

import unittest
from unittest.mock import MagicMock, patch

from app.agents.rcm_pipeline import run_rcm_pipeline
from app.config import Settings
from app.schemas.coding import CodingAgentRequest, CodingAgentResponse


class TestRcmPipeline(unittest.TestCase):
    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_pipeline_runs_linear(self, mock_coding_llm: MagicMock, mock_pa_llm: MagicMock) -> None:
        mock_coding_llm.return_value = {
            "cdt_codes": ["D1110"],
            "icd10_codes": ["K02.9"],
            "confidence": 0.9,
            "justification": "Prophy",
        }
        mock_pa_llm.return_value = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": [],
            "risk_level": "low",
            "risk_reason": "Preventive",
        }
        settings = Settings(openrouter_api_key="x")
        req = CodingAgentRequest(
            clinical_note="Routine cleaning",
            patient_age=30,
            insurance="Delta Dental PPO",
        )
        out = run_rcm_pipeline(settings, None, req)
        self.assertIsInstance(out.coding, CodingAgentResponse)
        self.assertEqual(out.prior_auth.status, "pending_review")
        self.assertIn("D1110", out.coding.cdt_codes)


if __name__ == "__main__":
    unittest.main()
