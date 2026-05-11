"""Tests for the Stedi 837 dental-claim adapter and ``submit_claim_tool`` integration.

These tests stub ``httpx.Client`` so no network traffic is made. The goal is to
verify:

1. ``build_dental_claim_payload`` produces a Stedi-shaped JSON body that matches
   the canonical ``ClaimStructure`` fields used by the agent layer.
2. ``submit_dental_claim`` raises ``StediClaimsError`` on transport failure,
   non-2xx responses, and non-JSON or non-object payloads — and never logs raw
   PHI in those error paths.
3. ``submit_claim_tool`` honours ``Settings.stedi_claims_api_key``: when set, it
   delegates to the real adapter; on ``StediClaimsError`` it logs a scrubbed
   warning and falls back to the deterministic mock so the agent loop still
   completes.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx

from app.config import Settings
from app.integrations.stedi_claims import (
    StediClaimsError,
    build_dental_claim_payload,
    submit_dental_claim,
)
from app.tools.claim_tools import submit_claim_tool


def _claim_dict() -> dict:
    """A canonical ClaimStructure-shaped dict used across the suite."""
    return {
        "patient": {"name": "Jane Doe", "dob": "1987-03-15"},
        "provider": {"name": "Dr X", "npi": "1234567890"},
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
        "patient_address": {
            "line1": "101 Main St",
            "city": "Albany",
            "state": "NY",
            "postal_code": "12207",
        },
        "patient_sex": "F",
        "claim_frequency_code": "1",
        "place_of_service": "11",
        "patient_account_number": "ACCT-1001",
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
            },
            {
                "line_number": 2,
                "service_date": "2026-01-20",
                "cdt_code": "D2740",
                "units": Decimal("1"),
                "charge_amount": Decimal("1100.00"),
                "diagnosis_pointers": [1],
                "tooth_number": "8",
                "surface": "MOD",
                "prior_auth_number": "PA-9001",
            },
        ],
        "total_charge_amount": Decimal("1225.00"),
        "codes": {"cdt": ["D1110", "D2740"], "icd10": ["K02.9"]},
    }


class BuildPayloadTests(unittest.TestCase):
    def test_canonical_payload_shape(self) -> None:
        body = build_dental_claim_payload(_claim_dict())

        self.assertEqual(body["tradingPartnerServiceId"], "DDPPO01")
        self.assertEqual(body["billing"]["npi"], "1234567890")
        self.assertEqual(body["billing"]["employerId"], "123456789")
        self.assertEqual(body["billing"]["address"]["postalCode"], "12208")

        # Subscriber relationship code maps "self" → 18.
        self.assertEqual(body["subscriber"]["individualRelationshipCode"], "18")
        # DOBs are emitted as YYYYMMDD without dashes.
        self.assertEqual(body["subscriber"]["dateOfBirth"], "19870315")
        self.assertEqual(body["patient"]["dateOfBirth"], "19870315")

        claim_info = body["claimInformation"]
        self.assertEqual(claim_info["claimChargeAmount"], "1225.00")
        self.assertEqual(claim_info["placeOfServiceCode"], "11")
        self.assertEqual(claim_info["claimFrequencyCode"], "1")
        # Diagnosis: first → ABK (principal), subsequent → ABF.
        self.assertEqual(
            claim_info["healthCareCodeInformation"][0],
            {"diagnosisTypeCode": "ABK", "diagnosisCode": "K029"},
        )

        # Service lines map cleanly and preserve PA / tooth / surface.
        lines = claim_info["serviceLines"]
        self.assertEqual(len(lines), 2)
        line2 = lines[1]
        self.assertEqual(line2["professionalService"]["procedureCode"], "D2740")
        self.assertEqual(line2["professionalService"]["lineItemChargeAmount"], "1100.00")
        self.assertEqual(line2["serviceDateInformation"]["serviceDate"], "20260120")
        self.assertEqual(line2["toothInformation"]["toothCode"], "8")
        self.assertEqual(line2["toothInformation"]["toothSurface"], "MOD")
        self.assertEqual(
            line2["referenceInformation"][0],
            {
                "referenceIdentificationQualifier": "G1",
                "referenceIdentification": "PA-9001",
            },
        )

    def test_relationship_code_fallback_defaults_to_self(self) -> None:
        claim = _claim_dict()
        claim["subscriber"]["relationship_to_patient"] = None
        body = build_dental_claim_payload(claim)
        self.assertEqual(body["subscriber"]["individualRelationshipCode"], "18")

    def test_relationship_code_spouse_and_child(self) -> None:
        claim = _claim_dict()
        claim["subscriber"]["relationship_to_patient"] = "spouse"
        self.assertEqual(
            build_dental_claim_payload(claim)["subscriber"]["individualRelationshipCode"],
            "01",
        )
        claim["subscriber"]["relationship_to_patient"] = "child"
        self.assertEqual(
            build_dental_claim_payload(claim)["subscriber"]["individualRelationshipCode"],
            "19",
        )


class SubmitDentalClaimTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            stedi_claims_api_key="key-abc",
            stedi_claims_test_header=True,
            stedi_claims_timeout_seconds=5.0,
        )

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_success_returns_normalised_response(self, mock_client_cls: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "controlNumber": "0001",
            "status": "ACCEPTED",
            "tradingPartnerServiceId": "DDPPO01",
            # Echoed PHI fields that MUST be stripped from the audit excerpt.
            "subscriber": {"memberId": "MEM12345"},
            "patient": {"firstName": "Jane"},
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        result = submit_dental_claim(
            _claim_dict(), self._settings(), idempotency_key="enc-1"
        )

        self.assertEqual(result["claim_id"], "0001")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["submission_channel"], "stedi_dental")
        self.assertNotIn("subscriber", result["raw"])
        self.assertNotIn("patient", result["raw"])
        self.assertEqual(result["raw"]["controlNumber"], "0001")

        _args, kwargs = mock_client.post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Key key-abc")
        self.assertEqual(kwargs["headers"]["stedi-test"], "true")
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], "enc-1")

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_success_without_control_number_synthesises_id(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ACCEPTED"}
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        result = submit_dental_claim(_claim_dict(), self._settings())
        self.assertTrue(result["claim_id"].startswith("CLM"))

    def test_missing_api_key_raises(self) -> None:
        s = Settings(stedi_claims_api_key=None)
        with self.assertRaises(StediClaimsError) as cm:
            submit_dental_claim(_claim_dict(), s)
        self.assertIn("STEDI_CLAIMS_API_KEY", cm.exception.message)

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_transport_error_raises(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("tcp closed")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with self.assertRaises(StediClaimsError) as cm:
            submit_dental_claim(_claim_dict(), self._settings())
        self.assertIn("transport failure", cm.exception.message)
        self.assertIsNone(cm.exception.status_code)

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_http_400_raises_with_status_and_body(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error":"missing payer"}'
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with self.assertRaises(StediClaimsError) as cm:
            submit_dental_claim(_claim_dict(), self._settings())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("missing payer", cm.exception.body or "")

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_non_json_response_raises(self, mock_client_cls: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>500</html>"
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with self.assertRaises(StediClaimsError) as cm:
            submit_dental_claim(_claim_dict(), self._settings())
        self.assertIn("non-JSON", cm.exception.message)

    @patch("app.integrations.stedi_claims.httpx.Client")
    def test_non_object_json_raises(self, mock_client_cls: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ["unexpected", "list"]
        mock_response.text = '["unexpected","list"]'
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with self.assertRaises(StediClaimsError) as cm:
            submit_dental_claim(_claim_dict(), self._settings())
        self.assertIn("non-object", cm.exception.message)


class SubmitClaimToolIntegrationTests(unittest.TestCase):
    def test_no_api_key_returns_mock(self) -> None:
        out = submit_claim_tool(_claim_dict(), settings=Settings())
        self.assertEqual(out["submission_channel"], "stedi_mock")
        self.assertEqual(out["status"], "submitted")
        self.assertTrue(out["claim_id"].startswith("CLM"))

    @patch("app.tools.claim_tools.submit_dental_claim")
    def test_with_api_key_delegates_to_real_adapter(
        self, mock_submit: MagicMock
    ) -> None:
        mock_submit.return_value = {
            "claim_id": "0007",
            "status": "submitted",
            "submission_channel": "stedi_dental",
            "raw": {"status": "ACCEPTED"},
        }
        s = Settings(stedi_claims_api_key="key-real")
        out = submit_claim_tool(_claim_dict(), settings=s)
        mock_submit.assert_called_once()
        self.assertEqual(out["submission_channel"], "stedi_dental")
        self.assertEqual(out["claim_id"], "0007")

    @patch("app.tools.claim_tools.submit_dental_claim")
    def test_falls_back_to_mock_on_stedi_error(self, mock_submit: MagicMock) -> None:
        mock_submit.side_effect = StediClaimsError(
            message="HTTP 500",
            status_code=500,
            body="server exploded",
        )
        s = Settings(stedi_claims_api_key="key-real")
        with self.assertLogs("app.tools.claim_tools", level="WARNING") as logs:
            out = submit_claim_tool(_claim_dict(), settings=s)
        self.assertEqual(out["submission_channel"], "stedi_mock")
        self.assertTrue(out["claim_id"].startswith("CLM"))
        joined = "\n".join(logs.output)
        self.assertIn("Stedi claim submission failed", joined)
        self.assertIn("HTTP 500", joined)


if __name__ == "__main__":
    unittest.main()
