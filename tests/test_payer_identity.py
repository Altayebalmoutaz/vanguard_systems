"""Canonical payer identity resolution (Step 1)."""

from __future__ import annotations

import unittest

from app.integrations.payer_identity import (
    enrich_queue_payer_labels,
    normalize_insurance_alias,
    payer_label_needs_directory_name,
    resolve_canonical_payer_id,
)


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


class TestPayerLabelEnrichment(unittest.TestCase):
    def test_needs_directory_name(self) -> None:
        # Bare Stedi / ElectID codes need a display name.
        self.assertTrue(payer_label_needs_directory_name("10134"))
        self.assertTrue(payer_label_needs_directory_name("128CA"))
        self.assertTrue(payer_label_needs_directory_name(""))
        self.assertTrue(payer_label_needs_directory_name(None))
        # Human-readable carrier names are left alone.
        self.assertFalse(payer_label_needs_directory_name("MetLife"))
        self.assertFalse(payer_label_needs_directory_name("Delta Dental MA"))

    def test_enrich_replaces_bare_ids_with_display_names(self) -> None:
        from unittest.mock import MagicMock

        sb = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.in_.return_value = chain
        chain.execute.return_value = MagicMock(
            data=[
                {
                    "payer_id": "10134",
                    "trading_partner_service_id": "10134",
                    "display_name": "MetLife Dental Family",
                }
            ]
        )
        sb.table.return_value = chain

        rows = [
            {"payer_label": "10134", "primary_payer_id": "10134"},
            {"payer_label": "MetLife", "primary_payer_id": "MetLife"},
        ]
        out = enrich_queue_payer_labels(rows, supabase=sb)

        self.assertEqual(out[0]["payer_label"], "MetLife Dental Family")
        # Human-readable labels are untouched.
        self.assertEqual(out[1]["payer_label"], "MetLife")

    def test_enrich_noop_without_supabase(self) -> None:
        rows = [{"payer_label": "10134", "primary_payer_id": "10134"}]
        self.assertEqual(enrich_queue_payer_labels(rows, supabase=None), rows)


if __name__ == "__main__":
    unittest.main()
