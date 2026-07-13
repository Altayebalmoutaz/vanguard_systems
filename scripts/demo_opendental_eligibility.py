"""Run OpenDental -> eligibility-agent demo calls and print a summary table."""

from __future__ import annotations

import argparse
import json

import httpx

_LAYER_LABELS = {
    "benefit_notes": "L2 BenefitNotes",
    "subscriber_note": "L2 SubscNote",
    "insverifies": "L1 InsVerify",
    "commlog": "L2 Commlog",
    "benefits_grid": "L3 Benefits grid",
    "insadjust": "L4 InsAdjust",
}


def _layer_status(notes: dict[str, object], key: str) -> str:
    block = notes.get(key)
    if not isinstance(block, dict):
        return "off"
    if block.get("error"):
        return "error"
    if block.get("skipped"):
        return f"skip:{block.get('skipped')}"
    if key == "benefits_grid":
        actions = block.get("actions") or []
        if isinstance(actions, list) and actions:
            created = sum(
                1 for a in actions if isinstance(a, dict) and a.get("action") == "created"
            )
            updated = sum(
                1 for a in actions if isinstance(a, dict) and a.get("action") == "updated"
            )
            skipped = sum(
                1
                for a in actions
                if isinstance(a, dict) and a.get("action") == "skipped_human_edit"
            )
            return f"ok c={created} u={updated} skip={skipped}"
    return "ok"


def _row(result: dict) -> tuple[str, str, str, str, str]:
    primary = result.get("primary") or {}
    routing = (primary.get("routing") or {}).get("status", "-")
    check_id = str(primary.get("check_id") or "-")
    opd = result.get("opendental") or {}
    writeback = opd.get("write_back_result") or {}
    insverify_num = str(writeback.get("InsVerifyNum", "-"))
    notes = opd.get("write_back_notes") or {}
    layers = " | ".join(
        f"{_LAYER_LABELS[k]}={_layer_status(notes, k)}"
        for k in (
            "insverifies",
            "benefit_notes",
            "subscriber_note",
            "commlog",
            "benefits_grid",
            "insadjust",
        )
    )
    return routing, check_id, insverify_num, layers, json.dumps(opd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo OpenDental eligibility route")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Eligibility API base URL"
    )
    parser.add_argument("--pat-nums", nargs="+", type=int, required=True, help="OpenDental PatNums")
    parser.add_argument(
        "--write-back", action="store_true", help="Request write-back to OpenDental"
    )
    args = parser.parse_args()

    print("pat_num | routing | check_id | insverify_num | layers")
    print("-" * 120)
    with httpx.Client(timeout=45.0) as client:
        for pat_num in args.pat_nums:
            payload = {
                "pat_num": pat_num,
                "trigger_event": "PRE_APPOINTMENT",
                "cdt_codes": ["D1110"],
                "write_back": args.write_back,
            }
            resp = client.post(
                f"{args.base_url.rstrip('/')}/eligibility-agent/eligibility/from-opendental",
                json=payload,
            )
            if resp.status_code >= 400:
                print(f"{pat_num} | ERROR {resp.status_code} | - | - | -")
                print(resp.text)
                continue
            out = resp.json()
            routing, check_id, insverify_num, layers, _detail = _row(out)
            print(f"{pat_num} | {routing} | {check_id} | {insverify_num} | {layers}")


if __name__ == "__main__":
    main()
