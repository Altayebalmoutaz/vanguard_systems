"""PHI-safety tests for coding decision golden candidates."""

from __future__ import annotations

import json

from scripts.decisions_to_golden import _to_case


def test_to_case_scrubs_llm_explanations_and_justification() -> None:
    miss = {
        "coding_run_id": "66666666-6666-6666-6666-666666666666",
        "line_id": "1",
        "action": "edited",
        "suggested_cdt": "D2140",
        "final_cdt": "D2391",
        "request_payload": {
            "request_id": "77777777-7777-7777-7777-777777777777",
            "practice_id": "practice-a",
            "patient_id": "patient-a",
            "provider_id": "provider-a",
            "procedures": [{"line_id": "1", "findings": ["caries"]}],
        },
        "response_payload": {
            "recommendations": [
                {
                    "line_id": "1",
                    "cdt_code": "D2140",
                    "explanation": "Patient SSN 123-45-6789 supports this code",
                }
            ],
            "overall_confidence": 0.8,
            "justification": "Reviewed member 123-45-6789",
        },
    }

    case = _to_case(miss)

    assert case is not None
    serialized = json.dumps(case["mock_llm"])
    assert "123-45-6789" not in serialized
    assert "REDACTED_SSN" in serialized
