#!/usr/bin/env python3
"""Clinic fee-schedule simulation seeder for Layer 5 / Track E demos.

Writes fake INN/OON + contracted fees into Supabase for ``vgd_mock_brooklyn``
(Cigna ``62308``) without touching OpenDental FeeSched.

Usage:
  py -3.12 scripts/seed_clinic_sim.py status
  py -3.12 scripts/seed_clinic_sim.py reset
  py -3.12 scripts/seed_clinic_sim.py apply --scenario inn_happy
  py -3.12 scripts/seed_clinic_sim.py apply --scenario oon_ucr
  py -3.12 scripts/seed_clinic_sim.py apply --scenario missing_fees

Then re-run eligibility + writeback (example PatNum 37)::

  py -3.12 -c "import json,urllib.request; body={'pat_num':37,'trigger_event':'PRE_APPOINTMENT','cdt_codes':['D0220'],'practice_id':'vgd_mock_brooklyn','rendering_provider_npi':'1104023674','write_back':True}; req=urllib.request.Request('http://127.0.0.1:8000/eligibility-agent/eligibility/from-opendental',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST'); print(urllib.request.urlopen(req,timeout=120).read().decode()[:2000])"

For ``missing_fees``, keep ``ELIGIBILITY_UCR_FALLBACK_ENABLED`` off so UCR fill does not hide gaps.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from app.config import get_settings
from app.db.connection import NeonNotConfiguredError, neon_connection
from app.eligibility.mock_clinic import DEFAULT_MOCK_PRACTICE_ID, DEFAULT_MOCK_RENDERING_NPI

PRACTICE_ID = DEFAULT_MOCK_PRACTICE_ID
NPI = DEFAULT_MOCK_RENDERING_NPI
PAYER_ID = "62308"
SIM_TAG = "[clinic_sim]"
# Fee rows have no notes column — sim overlays use this effective_date (wins over 2026-01-01).
SIM_FEE_EFFECTIVE = date(2026, 7, 26)
BASELINE_FEE_EFFECTIVE = date(2026, 1, 1)

# Original migration-041 values for the primary Cigna network row (restored on reset).
ORIGINAL_NETWORK = {
    "in_network_for_fees": True,
    "contract_label": "Mock Cigna PPO",
    "notes": "Primary rendering NPI fee network",
}

# Baseline 62308 fees currently in DB for demo CDTs (restored after missing_fees).
BASELINE_FEES: dict[str, float] = {
    "D0220": 45.0,
    "D1110": 120.0,
    "D2391": 210.0,
    "D2740": 980.0,
}

# Plan demo CDTs (D2140 may be net-new).
DEMO_CDTS = ("D0220", "D1110", "D2391", "D2140", "D2740")

INN_HAPPY_FEES: dict[str, float] = {
    "D0220": 45.0,
    "D1110": 140.0,
    "D2391": 220.0,
    "D2140": 150.0,
    "D2740": 1100.0,
}

# Higher UCR-style amounts so OON estimates visibly differ from INN contracted.
OON_UCR_FEES: dict[str, float] = {
    "D0220": 90.0,
    "D1110": 200.0,
    "D2391": 350.0,
    "D2140": 250.0,
    "D2740": 1400.0,
}

SCENARIOS = ("inn_happy", "oon_ucr", "missing_fees")

RERUN_HINT = """
Next step — re-run OD eligibility + writeback (PatNum 37 / Cigna):

  py -3.12 -c "import json,urllib.request; body={'pat_num':37,'trigger_event':'PRE_APPOINTMENT','cdt_codes':['D0220'],'practice_id':'vgd_mock_brooklyn','rendering_provider_npi':'1104023674','write_back':True}; req=urllib.request.Request('http://127.0.0.1:8000/eligibility-agent/eligibility/from-opendental',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST'); print(urllib.request.urlopen(req,timeout=120).read().decode()[:2000])"

For missing_fees: ensure ELIGIBILITY_UCR_FALLBACK_ENABLED is off.
""".strip()


def _conn():
    settings = get_settings()
    return neon_connection(settings, practice_id=PRACTICE_ID, bypass_rls=True)


def _print_status(cur: Any) -> None:
    cur.execute(
        """
        select rendering_provider_npi, payer_id, in_network_for_fees,
               contract_label, notes, effective_from, effective_to
        from rcm.provider_payer_network
        where practice_id = %s and payer_id = %s
        order by rendering_provider_npi
        """,
        (PRACTICE_ID, PAYER_ID),
    )
    rows = cur.fetchall()
    print(f"provider_payer_network ({PRACTICE_ID} / {PAYER_ID}):")
    if not rows:
        print("  (none)")
    for r in rows:
        print(f"  npi={r[0]} inn={r[2]} label={r[3]!r} notes={r[4]!r} from={r[5]} to={r[6]}")

    cur.execute(
        """
        select cdt_code, contracted_fee, effective_date
        from rcm.payer_fee_schedules
        where payer_id = %s
        order by cdt_code, effective_date
        """,
        (PAYER_ID,),
    )
    fees = cur.fetchall()
    sim_fees = [f for f in fees if f[2] == SIM_FEE_EFFECTIVE]
    print(f"payer_fee_schedules ({PAYER_ID}): {len(fees)} total, {len(sim_fees)} sim overlays")
    for cdt, fee, eff in fees:
        marker = " [sim]" if eff == SIM_FEE_EFFECTIVE else ""
        print(f"  {cdt} ${float(fee):.2f} @ {eff}{marker}")


def cmd_status() -> int:
    with _conn() as conn, conn.cursor() as cur:
        _print_status(cur)
    return 0


def _delete_sim_fee_overlays(cur: Any) -> int:
    cur.execute(
        """
        delete from rcm.payer_fee_schedules
        where payer_id = %s and effective_date = %s
        """,
        (PAYER_ID, SIM_FEE_EFFECTIVE),
    )
    return cur.rowcount


def _upsert_fees(cur: Any, fees: dict[str, float], *, effective: date) -> None:
    for cdt, amount in fees.items():
        cur.execute(
            """
            insert into rcm.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
            values (%s, %s, %s, %s)
            on conflict (payer_id, cdt_code, effective_date)
            do update set contracted_fee = excluded.contracted_fee
            """,
            (PAYER_ID, cdt, amount, effective),
        )


def _restore_baseline_demo_fees(cur: Any) -> None:
    """Re-insert baseline demo CDT fees if missing_fees removed them."""
    for cdt, amount in BASELINE_FEES.items():
        cur.execute(
            """
            insert into rcm.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
            values (%s, %s, %s, %s)
            on conflict (payer_id, cdt_code, effective_date)
            do update set contracted_fee = excluded.contracted_fee
            """,
            (PAYER_ID, cdt, amount, BASELINE_FEE_EFFECTIVE),
        )
    # D2140 is sim-only; remove if left behind without a baseline.
    cur.execute(
        """
        delete from rcm.payer_fee_schedules
        where payer_id = %s and cdt_code = 'D2140' and effective_date = %s
        """,
        (PAYER_ID, BASELINE_FEE_EFFECTIVE),
    )


def _restore_network_row(cur: Any) -> None:
    cur.execute(
        """
        update rcm.provider_payer_network
        set in_network_for_fees = %s,
            contract_label = %s,
            notes = %s,
            updated_at = now()
        where practice_id = %s
          and rendering_provider_npi = %s
          and payer_id = %s
          and coalesce(provider_service_location_key, '') = ''
        """,
        (
            ORIGINAL_NETWORK["in_network_for_fees"],
            ORIGINAL_NETWORK["contract_label"],
            ORIGINAL_NETWORK["notes"],
            PRACTICE_ID,
            NPI,
            PAYER_ID,
        ),
    )
    if cur.rowcount == 0:
        cur.execute(
            """
            insert into rcm.provider_payer_network (
              practice_id, rendering_provider_npi, payer_id,
              provider_service_location_key, in_network_for_fees,
              contract_label, notes, effective_from, effective_to
            )
            values (%s, %s, %s, null, %s, %s, %s, %s, null)
            """,
            (
                PRACTICE_ID,
                NPI,
                PAYER_ID,
                ORIGINAL_NETWORK["in_network_for_fees"],
                ORIGINAL_NETWORK["contract_label"],
                ORIGINAL_NETWORK["notes"],
                BASELINE_FEE_EFFECTIVE,
            ),
        )


def _set_network(cur: Any, *, in_network: bool, scenario: str) -> None:
    label = f"{SIM_TAG} {scenario} Cigna"
    notes = f"{SIM_TAG} clinic fee sim scenario={scenario}"
    cur.execute(
        """
        update rcm.provider_payer_network
        set in_network_for_fees = %s,
            contract_label = %s,
            notes = %s,
            updated_at = now()
        where practice_id = %s
          and rendering_provider_npi = %s
          and payer_id = %s
          and coalesce(provider_service_location_key, '') = ''
        """,
        (in_network, label, notes, PRACTICE_ID, NPI, PAYER_ID),
    )
    if cur.rowcount == 0:
        cur.execute(
            """
            insert into rcm.provider_payer_network (
              practice_id, rendering_provider_npi, payer_id,
              provider_service_location_key, in_network_for_fees,
              contract_label, notes, effective_from, effective_to
            )
            values (%s, %s, %s, null, %s, %s, %s, %s, null)
            """,
            (
                PRACTICE_ID,
                NPI,
                PAYER_ID,
                in_network,
                label,
                notes,
                BASELINE_FEE_EFFECTIVE,
            ),
        )


def cmd_reset() -> int:
    with _conn() as conn, conn.cursor() as cur:
        n_fees = _delete_sim_fee_overlays(cur)
        # Restore demo CDTs removed by missing_fees (and any zeroed baselines).
        cur.execute(
            """
                delete from rcm.payer_fee_schedules
                where payer_id = %s
                  and cdt_code = any(%s)
                  and effective_date = %s
                """,
            (PAYER_ID, list(DEMO_CDTS), BASELINE_FEE_EFFECTIVE),
        )
        _restore_baseline_demo_fees(cur)
        _restore_network_row(cur)
        # Clear any leftover sim-tagged network rows (associate NPI left alone).
        conn.commit()
        print(f"reset: removed {n_fees} sim fee overlay(s); restored network + baseline demo fees")
        _print_status(cur)
    print()
    print(RERUN_HINT)
    return 0


def _apply_inn_happy(cur: Any) -> None:
    _delete_sim_fee_overlays(cur)
    _restore_baseline_demo_fees(cur)
    _set_network(cur, in_network=True, scenario="inn_happy")
    _upsert_fees(cur, INN_HAPPY_FEES, effective=SIM_FEE_EFFECTIVE)


def _apply_oon_ucr(cur: Any) -> None:
    _delete_sim_fee_overlays(cur)
    _restore_baseline_demo_fees(cur)
    _set_network(cur, in_network=False, scenario="oon_ucr")
    _upsert_fees(cur, OON_UCR_FEES, effective=SIM_FEE_EFFECTIVE)


def _apply_missing_fees(cur: Any) -> None:
    """INN on, but no usable contracted fees for demo CDTs."""
    _delete_sim_fee_overlays(cur)
    _set_network(cur, in_network=True, scenario="missing_fees")
    # Remove baseline + any overlays for demo CDTs so Layer 5 sees a real gap.
    cur.execute(
        """
        delete from rcm.payer_fee_schedules
        where payer_id = %s and cdt_code = any(%s)
        """,
        (PAYER_ID, list(DEMO_CDTS)),
    )


def cmd_apply(scenario: str) -> int:
    if scenario not in SCENARIOS:
        print(f"unknown scenario {scenario!r}; choose from {SCENARIOS}", file=sys.stderr)
        return 2
    with _conn() as conn, conn.cursor() as cur:
        if scenario == "inn_happy":
            _apply_inn_happy(cur)
        elif scenario == "oon_ucr":
            _apply_oon_ucr(cur)
        else:
            _apply_missing_fees(cur)
        conn.commit()
        print(f"applied scenario={scenario}")
        _print_status(cur)
    print()
    print(RERUN_HINT)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed clinic fee/network simulation rows for vgd_mock_brooklyn + Cigna 62308",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RERUN_HINT,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show current network + fee rows for Cigna 62308")
    sub.add_parser("reset", help="Remove sim overlays and restore baseline network/fees")

    apply_p = sub.add_parser("apply", help="Reset sim tags then apply a named scenario")
    apply_p.add_argument(
        "--scenario",
        required=True,
        choices=SCENARIOS,
        help="inn_happy | oon_ucr | missing_fees",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return cmd_status()
        if args.command == "reset":
            return cmd_reset()
        return cmd_apply(args.scenario)
    except NeonNotConfiguredError:
        print("DATABASE_URL / NEON_DATABASE_URL is not configured", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
