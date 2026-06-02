"""
Coverage for the error/branching paths inside :mod:`app.agents.rcm_pipeline`.

The happy paths for ``run_rcm_pipeline`` and ``run_full_rcm_pipeline`` are
exercised by ``tests/test_rcm_pipeline.py`` and ``tests/test_claim_agent.py``.
This module focuses on:

* :func:`_resolve_claim_context` rejecting requests that lack both direct
  context and a usable encounter snapshot.
* :func:`_fetch_claim_snapshot` falling back from the RPC path to the
  ``claim_intake_snapshot`` table, and its error propagation.
* The Pydantic-validation error path inside :func:`_resolve_claim_context`
  (snapshot present but malformed).

All tests use mocked Supabase clients — no network or DB access.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.agents.rcm_pipeline import (
    _fetch_claim_snapshot,
    _resolve_claim_context,
    run_full_rcm_pipeline,
)
from app.config import Settings
from app.schemas.claim import FullRcmPipelineRequest


def _minimal_full_request(**overrides) -> FullRcmPipelineRequest:
    base = {
        "clinical_note": "Routine cleaning",
        "patient_age": 30,
        "insurance": "Delta Dental PPO",
        "encounter_id": "enc-zzz",
    }
    base.update(overrides)
    return FullRcmPipelineRequest.model_validate(base)


class ResolveClaimContextErrors(unittest.TestCase):
    def test_missing_encounter_and_direct_context_raises(self) -> None:
        # Schema-level validator forbids constructing the request, so we test the
        # _resolve_claim_context helper directly by passing a dummy object that
        # mimics the validated state but with no encounter_id / direct context.
        dummy = MagicMock()
        dummy.encounter_id = None
        dummy.patient = None
        dummy.provider = None
        dummy.billing = None
        with self.assertRaises(RuntimeError) as cm:
            _resolve_claim_context(dummy, supabase=None)
        self.assertIn("provide patient/provider/billing or encounter_id", str(cm.exception))

    def test_encounter_id_without_supabase_raises(self) -> None:
        request = _minimal_full_request()
        with self.assertRaises(RuntimeError) as cm:
            _resolve_claim_context(request, supabase=None)
        self.assertIn("Supabase client is required", str(cm.exception))

    @patch("app.agents.rcm_pipeline._fetch_claim_snapshot")
    def test_snapshot_missing_raises(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = None
        request = _minimal_full_request()
        with self.assertRaises(RuntimeError) as cm:
            _resolve_claim_context(request, supabase=MagicMock())
        self.assertIn("No claim intake snapshot", str(cm.exception))
        self.assertIn("enc-zzz", str(cm.exception))

    @patch("app.agents.rcm_pipeline._fetch_claim_snapshot")
    def test_snapshot_not_ready_raises(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = {"ready_for_claim": False, "patient": {}}
        request = _minimal_full_request()
        with self.assertRaises(RuntimeError) as cm:
            _resolve_claim_context(request, supabase=MagicMock())
        self.assertIn("not ready_for_claim", str(cm.exception))

    @patch("app.agents.rcm_pipeline._fetch_claim_snapshot")
    def test_snapshot_invalid_pydantic_raises(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = {
            "ready_for_claim": True,
            "patient": {"name": "Jane Doe"},  # missing required `dob`
            "subscriber": {},
            "payer": {},
            "billing_provider": {},
            "rendering_provider": {},
            "claim_header": {},
            "diagnosis_codes": [],
            "service_lines": [],
            "financials": {},
        }
        request = _minimal_full_request()
        with self.assertRaises(RuntimeError) as cm:
            _resolve_claim_context(request, supabase=MagicMock())
        self.assertIn("Invalid claim snapshot", str(cm.exception))
        self.assertIn("enc-zzz", str(cm.exception))


class FetchClaimSnapshotPaths(unittest.TestCase):
    def _make_supabase(
        self, *, rpc_data=None, rpc_raises=False, table_data=None, table_raises=False
    ) -> MagicMock:
        supabase = MagicMock()
        if rpc_raises:
            supabase.rpc.side_effect = RuntimeError("rpc unavailable")
        else:
            rpc_resp = MagicMock()
            rpc_resp.data = rpc_data
            supabase.rpc.return_value.execute.return_value = rpc_resp
        # Table fallback chain: .table(...).select(...).eq(...).limit(...).execute()
        if table_raises:
            supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = RuntimeError(
                "table unavailable"
            )
        else:
            table_resp = MagicMock()
            table_resp.data = table_data
            supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = table_resp
        return supabase

    def test_rpc_returns_dict(self) -> None:
        snapshot = {"ready_for_claim": True, "patient": {"name": "x", "dob": "2000-01-01"}}
        supabase = self._make_supabase(rpc_data=snapshot)
        out = _fetch_claim_snapshot(supabase, "enc-1")
        self.assertEqual(out, snapshot)
        supabase.rpc.assert_called_once_with(
            "get_claim_intake_snapshot", {"p_encounter_id": "enc-1"}
        )
        supabase.table.assert_not_called()

    def test_rpc_returns_list_with_dict(self) -> None:
        snapshot = {"ready_for_claim": True, "patient": {"name": "x", "dob": "2000-01-01"}}
        supabase = self._make_supabase(rpc_data=[snapshot])
        out = _fetch_claim_snapshot(supabase, "enc-2")
        self.assertEqual(out, snapshot)

    def test_rpc_returns_unusable_then_table_fallback_succeeds(self) -> None:
        snapshot = {"ready_for_claim": True}
        # rpc returns None / non-dict / empty list -> fall through to table
        supabase = self._make_supabase(rpc_data=None, table_data=[snapshot])
        out = _fetch_claim_snapshot(supabase, "enc-3")
        self.assertEqual(out, snapshot)
        supabase.table.assert_called_once_with("claim_intake_snapshot")

    def test_rpc_raises_then_table_fallback_succeeds(self) -> None:
        snapshot = {"ready_for_claim": True}
        supabase = self._make_supabase(rpc_raises=True, table_data=[snapshot])
        out = _fetch_claim_snapshot(supabase, "enc-4")
        self.assertEqual(out, snapshot)

    def test_rpc_and_table_both_empty_returns_none(self) -> None:
        supabase = self._make_supabase(rpc_data=None, table_data=[])
        out = _fetch_claim_snapshot(supabase, "enc-5")
        self.assertIsNone(out)

    def test_table_raises_propagates_runtime_error(self) -> None:
        supabase = self._make_supabase(rpc_data=None, table_raises=True)
        with self.assertRaises(RuntimeError) as cm:
            _fetch_claim_snapshot(supabase, "enc-6")
        self.assertIn("Failed to load claim intake snapshot", str(cm.exception))
        self.assertIn("enc-6", str(cm.exception))


class FullPipelineDirectContext(unittest.TestCase):
    """Sanity check that the direct-context path skips the snapshot lookup entirely."""

    @patch("app.agents.rcm_pipeline._fetch_claim_snapshot")
    @patch("app.tools.prior_auth_tools.llm_prior_auth_decision")
    @patch("app.tools.coding_tools.llm_generate_codes")
    def test_direct_context_skips_snapshot_lookup(
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
        request = FullRcmPipelineRequest.model_validate(
            {
                "clinical_note": "Adult prophylaxis",
                "patient_age": 30,
                "insurance": "Delta Dental PPO",
                "patient": {"name": "Jane Doe", "dob": "1987-03-15"},
                "provider": {"name": "Dr X", "npi": "1234567890"},
                "billing": {
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
                            "units": "1",
                            "charge_amount": "125.00",
                            "diagnosis_pointers": [1],
                        }
                    ],
                    "total_charge_amount": "125.00",
                },
            }
        )
        settings = Settings(openrouter_api_key="x")
        out = run_full_rcm_pipeline(settings, MagicMock(), request)
        self.assertEqual(out.claim_draft.status, "draft")
        mock_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
