"""Canonical payer identity resolution (Step 1)."""

from __future__ import annotations

import unittest

from app.integrations.payer_identity import normalize_insurance_alias, resolve_canonical_payer_id


class TestPayerIdentity(unittest.TestCase):
    def test_normalize_insurance_alias(self) -> None:
        self.assertEqual(normalize_insurance_alias("  United   Healthcare  "), "united healthcare")
        self.assertEqual(normalize_insurance_alias(""), "")

    def test_resolve_returns_none_when_supabase_empty(self) -> None:
        """Without DB rows, resolution returns None (no crash)."""
        from unittest.mock import MagicMock

        sb = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        sb.table.return_value = chain

        self.assertIsNone(resolve_canonical_payer_id(sb, "unknown payer xyz"))


if __name__ == "__main__":
    unittest.main()
