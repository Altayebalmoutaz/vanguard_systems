"""Coding-agent accuracy scorecard.

Reads ``analytics.coding_scorecard`` (suggest runs joined to dentist decisions)
and computes the live metrics that define "are we near 98%?":

* ``top1`` — CDT top-1 line match vs the dentist's final code (over decided lines)
* ``coverage`` — share of lines the agent proposed a code for
* ``needs_info_rate`` — share of lines whose run was gated needs_info
* ``false_gap_rate`` — share of gapped lines the dentist approved unchanged
  (i.e. the gap was noise)

Usage:
    python -m app.coding.scorecard [--days 30] [--practice-id P] [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from app.config import Settings, get_settings
from app.db.connection import database_connection, get_neon_dsn


@dataclass
class Scorecard:
    scope: str
    lines_total: int
    lines_proposed: int
    lines_decided: int
    top1_hits: int
    lines_with_gap: int
    false_gaps: int
    lines_in_needs_info: int

    @property
    def top1(self) -> float | None:
        return self.top1_hits / self.lines_decided if self.lines_decided else None

    @property
    def coverage(self) -> float | None:
        return self.lines_proposed / self.lines_total if self.lines_total else None

    @property
    def needs_info_rate(self) -> float | None:
        return self.lines_in_needs_info / self.lines_total if self.lines_total else None

    @property
    def false_gap_rate(self) -> float | None:
        return self.false_gaps / self.lines_with_gap if self.lines_with_gap else None

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d.update(
            top1=self.top1,
            coverage=self.coverage,
            needs_info_rate=self.needs_info_rate,
            false_gap_rate=self.false_gap_rate,
        )
        return d


_SUMS = """
    coalesce(sum(lines_total), 0),
    coalesce(sum(lines_proposed), 0),
    coalesce(sum(lines_decided), 0),
    coalesce(sum(top1_hits), 0),
    coalesce(sum(lines_with_gap), 0),
    coalesce(sum(false_gaps), 0),
    coalesce(sum(lines_in_needs_info), 0)
"""


def _row_to_card(scope: str, row: tuple) -> Scorecard:
    return Scorecard(scope, *(int(v) for v in row))


def compute_scorecards(
    settings: Settings | None = None,
    *,
    days: int = 30,
    practice_id: str | None = None,
) -> dict[str, list[Scorecard]]:
    """Return {'overall': [...], 'by_payer': [...], 'by_family': [...]}."""
    app_settings = settings or get_settings()
    if not get_neon_dsn(app_settings):
        return {"overall": [], "by_payer": [], "by_family": []}

    where = "run_date >= (current_date - %s::int)"
    params: list[object] = [days]
    if practice_id:
        where += " and practice_id = %s"
        params.append(practice_id)

    out: dict[str, list[Scorecard]] = {}
    with database_connection(app_settings, bypass_rls=True) as conn, conn.cursor() as cur:
        cur.execute(f"select {_SUMS} from analytics.coding_scorecard where {where}", params)
        row = cur.fetchone()
        out["overall"] = [_row_to_card("overall", row)] if row else []

        cur.execute(
            f"select payer_id, {_SUMS} from analytics.coding_scorecard where {where} "
            "group by payer_id order by 2 desc",
            params,
        )
        out["by_payer"] = [_row_to_card(str(r[0]), r[1:]) for r in cur.fetchall()]

        cur.execute(
            f"select cdt_family, {_SUMS} from analytics.coding_scorecard where {where} "
            "group by cdt_family order by 2 desc",
            params,
        )
        out["by_family"] = [_row_to_card(str(r[0]), r[1:]) for r in cur.fetchall()]
    return out


def _pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v * 100:5.1f}%"


def _print_section(title: str, cards: list[Scorecard]) -> None:
    print(f"\n== {title} ==")
    print(
        f"{'scope':<12} {'top1':>7} {'cover':>7} {'needs':>7} {'falseg':>7} "
        f"{'decided':>8} {'total':>7}"
    )
    for c in cards:
        print(
            f"{c.scope:<12} {_pct(c.top1):>7} {_pct(c.coverage):>7} "
            f"{_pct(c.needs_info_rate):>7} {_pct(c.false_gap_rate):>7} "
            f"{c.lines_decided:>8} {c.lines_total:>7}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--practice-id", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cards = compute_scorecards(days=args.days, practice_id=args.practice_id)
    if args.json:
        print(
            json.dumps(
                {k: [c.to_dict() for c in v] for k, v in cards.items()},
                indent=2,
                default=str,
            )
        )
        return 0

    overall = cards["overall"][0] if cards["overall"] else None
    if overall is None or overall.lines_total == 0:
        print("No coding runs in window (or DATABASE_URL not configured).")
        return 0
    _print_section(f"Overall (last {args.days}d)", cards["overall"])
    _print_section("By payer", cards["by_payer"])
    _print_section("By CDT family", cards["by_family"])
    if overall.lines_decided == 0:
        print("\nNote: 0 decisions captured yet — POST /v1/decision to populate top-1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
