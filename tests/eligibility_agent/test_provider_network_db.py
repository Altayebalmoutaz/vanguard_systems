"""provider_payer_network resolution (Layer 5 fee path)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from app.eligibility.db import fetch_active_provider_payer_network


def test_fetch_prefers_location_key_row() -> None:
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {
                "practice_id": "p1",
                "rendering_provider_npi": "1104023674",
                "payer_id": "60054",
                "provider_service_location_key": None,
                "in_network_for_fees": True,
                "effective_from": "2026-01-01",
                "effective_to": None,
            },
            {
                "practice_id": "p1",
                "rendering_provider_npi": "1104023674",
                "payer_id": "60054",
                "provider_service_location_key": "site_main",
                "in_network_for_fees": False,
                "effective_from": "2026-01-01",
                "effective_to": None,
            },
        ]
    )

    out = fetch_active_provider_payer_network(
        supabase,
        practice_id="p1",
        rendering_provider_npi="1104023674",
        payer_trading_partner_id="60054",
        provider_service_location_key="site_main",
        as_of=date(2026, 6, 1),
    )
    assert out is not None
    assert out.get("provider_service_location_key") == "site_main"
    assert out.get("in_network_for_fees") is False
