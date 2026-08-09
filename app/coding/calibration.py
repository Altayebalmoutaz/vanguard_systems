"""Confidence calibration scaffolding for the coding agent.

The model emits a raw self-reported confidence. Whether "0.9" actually means a
90% chance the dentist keeps the code is an empirical question answered by
``agents.coding_decisions``. This module provides:

* ``compute_reliability_bins`` — bin decided lines by predicted confidence and
  measure the empirical top-1 hit rate per bin (a reliability diagram in data).
* ``calibrate`` — map a raw confidence through a stored calibration map. With no
  map it is the identity, so this is safe to wire in before any map exists.

A calibration map is intentionally simple (piecewise-linear over bin midpoints),
so it can be fit from ``compute_reliability_bins`` output and stored as JSON.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import pairwise

from app.config import Settings, get_settings
from app.db.connection import database_connection, get_neon_dsn


@dataclass
class ReliabilityBin:
    lower: float
    upper: float
    predicted_mean: float
    empirical_hit_rate: float
    count: int


# A calibration map is a sorted list of (raw_confidence, calibrated_confidence)
# anchor points; calibrate() linearly interpolates between them.
CalibrationMap = list[tuple[float, float]]


def calibrate(raw: float, cmap: CalibrationMap | None = None) -> float:
    """Map a raw confidence through ``cmap`` (identity if empty)."""
    value = max(0.0, min(1.0, float(raw)))
    if not cmap:
        return value
    points = sorted(cmap)
    if value <= points[0][0]:
        return max(0.0, min(1.0, points[0][1]))
    if value >= points[-1][0]:
        return max(0.0, min(1.0, points[-1][1]))
    for (x0, y0), (x1, y1) in pairwise(points):
        if x0 <= value <= x1:
            span = x1 - x0
            frac = 0.0 if span == 0 else (value - x0) / span
            return max(0.0, min(1.0, y0 + frac * (y1 - y0)))
    return value


def compute_reliability_bins(
    settings: Settings | None = None,
    *,
    days: int = 90,
    n_bins: int = 10,
    practice_id: str | None = None,
) -> list[ReliabilityBin]:
    """Reliability diagram data: predicted confidence vs actual top-1 hit rate."""
    app_settings = settings or get_settings()
    if not get_neon_dsn(app_settings):
        return []

    where = [
        "l.decided_at is not null",
        "l.suggested_cdt <> ''",
        "l.run_date >= (current_date - %s::int)",
    ]
    params: list[object] = [days]
    if practice_id:
        where.append("l.practice_id = %s")
        params.append(practice_id)
    where_sql = " and ".join(where)

    query = f"""
        with scored as (
          select
            width_bucket(l.confidence, 0.0, 1.0, %s) as b,
            l.confidence as conf,
            case when l.decision_action = 'approved'
                   or (l.final_cdt <> '' and l.final_cdt = l.suggested_cdt)
                 then 1 else 0 end as hit
          from analytics.coding_line_outcomes l
          where {where_sql}
        )
        select b, count(*)::int, avg(conf)::float, avg(hit)::float
        from scored group by b order by b
    """
    rows: list[tuple] = []
    with database_connection(app_settings, bypass_rls=True) as conn, conn.cursor() as cur:
        cur.execute(query, [n_bins, *params])
        rows = cur.fetchall()

    bins: list[ReliabilityBin] = []
    width = 1.0 / n_bins
    for b, count, pred_mean, hit_rate in rows:
        if b is None:
            continue
        idx = max(1, min(int(b), n_bins))
        bins.append(
            ReliabilityBin(
                lower=round((idx - 1) * width, 4),
                upper=round(idx * width, 4),
                predicted_mean=round(float(pred_mean or 0.0), 4),
                empirical_hit_rate=round(float(hit_rate or 0.0), 4),
                count=int(count),
            )
        )
    return bins


def fit_calibration_map(bins: list[ReliabilityBin], *, min_count: int = 20) -> CalibrationMap:
    """Fit a piecewise-linear map from predicted_mean -> empirical_hit_rate.

    Only bins with enough support contribute; returns [] (identity) if too sparse.
    """
    anchors = [(b.predicted_mean, b.empirical_hit_rate) for b in bins if b.count >= min_count]
    return sorted(anchors) if len(anchors) >= 2 else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding confidence reliability report")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--practice-id", default=None)
    args = parser.parse_args()

    bins = compute_reliability_bins(days=args.days, n_bins=args.bins, practice_id=args.practice_id)
    if not bins:
        print("No decided lines in window (or DATABASE_URL not configured).")
        return 0
    print(f"{'range':<14}{'pred':>8}{'actual':>8}{'n':>7}")
    for b in bins:
        print(
            f"[{b.lower:.2f},{b.upper:.2f}]{'':<3}{b.predicted_mean:>8.3f}"
            f"{b.empirical_hit_rate:>8.3f}{b.count:>7}"
        )
    fitted = fit_calibration_map(bins)
    print("\nfitted calibration map (raw -> calibrated):")
    print(json.dumps(fitted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
