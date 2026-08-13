"""Cross-impl parity: monolith in-process writeback vs sibling OD writeback service.

Skips when the sibling service package is not importable on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SIBLING = Path(__file__).resolve().parents[2] / "opendental-writeback-service"
if _SIBLING.is_dir() and str(_SIBLING) not in sys.path:
    sys.path.insert(0, str(_SIBLING))

try:
    from fastapi.testclient import TestClient
    from odwb import app as odwb_app_module
    from odwb.app import create_app as create_odwb_app
    from odwb.config import get_settings as get_odwb_settings

    _ODWB_AVAILABLE = True
except Exception:  # pragma: no cover - optional sibling
    _ODWB_AVAILABLE = False

from app.integrations.opendental.writeback import (  # noqa: E402
    run_opendental_writeback,
    writeback_has_failures,
)
from app.integrations.opendental.models import ODCommlogResponse, ODInsVerifyResponse  # noqa: E402

_CANONICAL = {
    "is_active": True,
    "response_complete": True,
    "coverage_percent": 100,
    "coinsurance": 0.0,
    "copay": 0,
    "deductible_total": 100,
    "deductible_remaining": 50,
    "annual_max_total": 1500,
    "annual_max_remaining": 1356,
}
_ESTIMATES = [
    {
        "cdt_code": "D1110",
        "patient_responsibility": 50,
        "insurance_pays": 70,
        "allowed_amount": 120,
    }
]


class _WBStub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def update_inssub_benefit_notes(self, ins_sub_num, plan_num, benefit_notes):  # type: ignore[no-untyped-def]
        self.calls.append("benefit_notes")
        return {"InsSubNum": ins_sub_num}

    def update_inssub_subscriber_note(self, ins_sub_num, plan_num, subscriber_note):  # type: ignore[no-untyped-def]
        self.calls.append("subscriber_note")
        return {"InsSubNum": ins_sub_num}

    def create_insverify(self, payload):  # type: ignore[no-untyped-def]
        self.calls.append("insverify")
        return ODInsVerifyResponse(InsVerifyNum=1, VerifyType=payload.VerifyType, FKey=payload.FKey)

    def create_commlog(self, pat_num, note, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append("commlog")
        return ODCommlogResponse(CommlogNum=9, PatNum=pat_num, Note=note)


@unittest.skipUnless(_ODWB_AVAILABLE, "opendental-writeback-service not on PYTHONPATH")
class CrossImplParityTests(unittest.TestCase):
    def test_service_layer12_matches_inprocess_structure(self) -> None:
        fixtures = _SIBLING / "tests" / "fixtures" / "opendental"
        allowlist = {
            "clinic-test": {
                "base_url": "https://api.opendental.com/api/v1",
                "customer_key_env": "OD_CUSTOMER_KEY_TEST",
            }
        }
        env = {
            "ODWB_API_KEY": "test-secret",
            "REQUIRE_AUTH": "true",
            "OPENDENTAL_DEVELOPER_KEY": "dev",
            "OD_CUSTOMER_KEY_TEST": "cust",
            "ODWB_TARGET_ALLOWLIST_JSON": json.dumps(allowlist),
            "ODWB_ALLOWED_HOSTS": "api.opendental.com",
            "OPENDENTAL_REPLAY_DIR": str(fixtures),
        }
        with patch.dict(os.environ, env, clear=False):
            get_odwb_settings.cache_clear()
            odwb_app_module._idempotency_cache.cache_clear()
            http = TestClient(create_odwb_app())
            api_resp = http.post(
                "/v1/writeback",
                headers={"Authorization": "Bearer test-secret"},
                json={
                    "source_agent": "eligibility",
                    "idempotency_key": "cross-impl-1",
                    "od_target": {"customer_key_ref": "clinic-test"},
                    "command": {
                        "type": "eligibility.apply_vob",
                        "payload": {
                            "pat_num": 24,
                            "primary_pat_plan_num": 101,
                            "primary_plan_num": 301,
                            "primary_ins_sub_num": 201,
                            "primary_result": {
                                "check_id": "c1",
                                "routing": {"status": "CLEARED"},
                                "canonical": _CANONICAL,
                                "procedure_estimates": _ESTIMATES,
                            },
                            "carrier_name": "Aetna",
                        },
                    },
                    "provenance": {"audit_rows": []},
                    "config": {"confidence_gating": False, "respect_manual_edits": True},
                },
            )
        self.assertEqual(api_resp.status_code, 200)
        api = api_resp.json()

        inproc = run_opendental_writeback(
            _WBStub(),  # type: ignore[arg-type]
            pat_num=24,
            primary_pat_plan_num=101,
            primary_plan_num=301,
            primary_ins_sub_num=201,
            primary_result={
                "check_id": "c1",
                "routing": {"status": "CLEARED"},
                "canonical": _CANONICAL,
                "procedure_estimates": _ESTIMATES,
            },
            carrier_name="Aetna",
        )
        self.assertEqual(api["partial_failure"], writeback_has_failures(inproc))
        self.assertEqual(
            api["write_back_result"]["benefit_notes"]["ins_sub_num"],
            inproc["benefit_notes"]["ins_sub_num"],
        )
        self.assertEqual(api["write_back_result"]["commlog"]["pat_num"], inproc["commlog"]["pat_num"])
        self.assertIn("Eligibility verified", api["write_back_result"]["subscriber_note"]["note_sent"])
        self.assertIn("Eligibility verified", inproc["subscriber_note"]["note_sent"])


if __name__ == "__main__":
    unittest.main()
