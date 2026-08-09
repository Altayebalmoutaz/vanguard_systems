"""Weekly coding-agent miss review.

Prints the accuracy scorecard plus the top confusion pairs (suggested -> final)
from dentist edits, so the weekly review can target the highest-leverage misses.

Usage:
    python -m scripts.weekly_miss_review [--days 7] [--practice-id P]
"""

from __future__ import annotations

import argparse

from app.coding.scorecard import _print_section, compute_scorecards
from app.config import get_settings
from app.db.connection import database_connection, get_neon_dsn


def _top_confusions(days: int, practice_id: str | None, limit: int = 20) -> list[tuple]:
    settings = get_settings()
    if not get_neon_dsn(settings):
        return []
    where = [
        "action = 'edited'",
        "final_cdt is not null",
        "decided_at >= now() - (%s || ' days')::interval",
    ]
    params: list[object] = [days]
    if practice_id:
        where.append("practice_id = %s")
        params.append(practice_id)
    where_sql = " and ".join(where)
    with database_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select coalesce(suggested_cdt, '(none)') as suggested,
                   final_cdt, count(*)::int as n
            from agents.coding_decisions
            where {where_sql}
            group by 1, 2
            order by n desc
            limit {int(limit)}
            """,
            params,
        )
        return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--practice-id", default=None)
    args = parser.parse_args()

    cards = compute_scorecards(days=args.days, practice_id=args.practice_id)
    overall = cards["overall"][0] if cards["overall"] else None
    if overall is None or overall.lines_total == 0:
        print("No coding activity in window (or DATABASE_URL not configured).")
        return 0

    print(f"=== Weekly coding review (last {args.days}d) ===")
    _print_section("Overall", cards["overall"])
    _print_section("By CDT family", cards["by_family"])

    confusions = _top_confusions(args.days, args.practice_id)
    print("\n== Top corrected codes (suggested -> final) ==")
    if not confusions:
        print("(no edits captured yet)")
    else:
        for suggested, final_cdt, n in confusions:
            print(f"  {suggested:>8} -> {final_cdt:<8} x{n}")

    if overall.lines_decided == 0:
        print("\nNote: capture decisions via POST /v1/decision to populate this review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
