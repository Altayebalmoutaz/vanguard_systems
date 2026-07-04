"""Tests for the Wave 3C ERA / Stedi 835 adapter skeleton."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.integrations.era import (
    Stedi835SandboxAdapter,
    Stedi835X12NotImplementedError,
    parse_stedi_835_json,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "era"


class EraAdapterTests(unittest.TestCase):
    def test_parse_stedi_835_json_stedi_claims_shape(self) -> None:
        payload = json.loads((_FIXTURES / "stedi_denied.json").read_text(encoding="utf-8"))
        out = parse_stedi_835_json(payload)
        self.assertEqual(out["status"], "denied")
        self.assertEqual(out["reason"], "missing_xray")

    def test_stedi835_sandbox_adapter_parses_json_bytes(self) -> None:
        raw = (_FIXTURES / "mock_paid.json").read_bytes()
        adapter = Stedi835SandboxAdapter()
        out = adapter.parse_remittance(raw)
        self.assertEqual(out, {"status": "paid", "reason": ""})

    def test_stedi835_sandbox_adapter_rejects_x12_stub(self) -> None:
        x12_stub = "ISA*00*          *00*          *ZZ*PAYER~ST*835*0001~"
        adapter = Stedi835SandboxAdapter()
        with self.assertRaises(Stedi835X12NotImplementedError):
            adapter.parse_remittance(x12_stub)


if __name__ == "__main__":
    unittest.main()
