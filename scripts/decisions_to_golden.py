"""Turn dentist edits into golden regression cases.

Reads recent misses (edited / rejected lines) from agents.coding_decisions joined
to agents.coding_runs and emits golden eval case candidates: the original request
as input, the original model output as ``mock_llm``, and the dentist's *final*
code as the expected top-1. Free-text fields are PHI-scrubbed.

Candidates land in ``evals/golden/coding/candidates/`` for human review before
being promoted into the CI golden set (do not auto-commit generated PHI-derived
fixtures without review).

Usage:
    python -m scripts.decisions_to_golden [--days 14] [--limit 50] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.connection import database_connection, get_neon_dsn
from app.security.phi import scrub_for_llm

_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "evals" / "golden" / "coding" / "candidates"


def _scrub_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact free-text clinical fields so generated fixtures are non-PHI."""
    req = json.loads(json.dumps(payload))  # deep copy
    if isinstance(req.get("supporting_note"), str):
        req["supporting_note"] = scrub_for_llm(req["supporting_note"])
    for proc in req.get("procedures") or []:
        if isinstance(proc.get("findings"), list):
            proc["findings"] = [scrub_for_llm(str(f)) for f in proc["findings"]]
    # Opaque ids that could correlate to a patient are not needed for a fixture.
    req["patient_id"] = "pat_fixture"
    req["provider_id"] = "prov_fixture"
    return req


def _fetch_misses(days: int, limit: int) -> list[dict[str, Any]]:
    settings = get_settings()
    if not get_neon_dsn(settings):
        print("DATABASE_URL not configured; nothing to export.")
        return []
    rows: list[dict[str, Any]] = []
    with database_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select d.coding_run_id, d.line_id, d.action, d.suggested_cdt,
                   d.final_cdt, r.request_payload, r.response_payload
            from agents.coding_decisions d
            join agents.coding_runs r on r.id = d.coding_run_id
            where d.action in ('edited', 'rejected')
              and d.decided_at >= now() - (%s || ' days')::interval
            order by d.decided_at desc
            limit %s
            """,
            (days, limit),
        )
        cols = [c.name for c in cur.description]
        for row in cur.fetchall():
            rows.append(dict(zip(cols, row, strict=False)))
    return rows


def _to_case(miss: dict[str, Any]) -> dict[str, Any] | None:
    request_payload = miss.get("request_payload")
    response_payload = miss.get("response_payload")
    if not isinstance(request_payload, dict) or not isinstance(response_payload, dict):
        return None
    line_id = str(miss.get("line_id"))
    final_cdt = miss.get("final_cdt")
    # mock_llm reproduces the ORIGINAL model output so the case is deterministic;
    # the expectation is the dentist's corrected code (the lesson to learn).
    mock_llm = scrub_for_llm(
        {
            "recommendations": response_payload.get("recommendations") or [],
            "overall_confidence": response_payload.get("overall_confidence") or 0.0,
            "justification": response_payload.get("justification") or "",
        }
    )
    if not isinstance(mock_llm, dict):  # defensive: recursive scrub preserves mappings
        return None
    expect: dict[str, Any] = {}
    if miss.get("action") == "edited" and final_cdt:
        expect["line_cdt"] = {line_id: str(final_cdt).upper().strip()}
    return {
        "name": f"decision_{miss.get('coding_run_id')}_{line_id}_{miss.get('action')}",
        "request": _scrub_request(request_payload),
        "mock_llm": mock_llm,
        "expect": expect,
        "_provenance": {
            "coding_run_id": str(miss.get("coding_run_id")),
            "line_id": line_id,
            "action": miss.get("action"),
            "suggested_cdt": miss.get("suggested_cdt"),
            "final_cdt": final_cdt,
            "note": "Auto-generated from a dentist decision; review before promoting.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--out", default=str(_DEFAULT_OUT))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for miss in _fetch_misses(args.days, args.limit):
        case = _to_case(miss)
        if case is None:
            continue
        path = out_dir / f"{case['name']}.json"
        path.write_text(json.dumps(case, indent=2, default=str), encoding="utf-8")
        written += 1

    print(f"Wrote {written} golden candidate(s) to {out_dir}")
    if written:
        print("Review + move approved cases up into evals/golden/coding/ to lock them into CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
