"""Prometheus-format metrics without a client-library dependency.

``GET /metrics`` renders the text exposition format (version 0.0.4) from two
sources:

* in-process counters (``inc()``) for events observed by this replica, and
* scrape-time gauges queried from Postgres (queue depth, DLQ size, request
  states, OpenDental connection health) plus the SSE subscriber count.

The route is mounted behind the standard auth dependency; point the Prometheus
scraper at it with an ``X-API-Key`` from ``INTERNAL_API_KEYS``.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

from fastapi import APIRouter
from starlette.responses import PlainTextResponse

from app.config import get_settings
from app.db.connection import get_neon_dsn, neon_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])

_counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_lock = threading.Lock()


def inc(name: str, labels: dict[str, str] | None = None, amount: int = 1) -> None:
    """Increment an in-process counter (e.g. ``inc('stedi_errors_total')``)."""
    key = (name, tuple(sorted((labels or {}).items())))
    with _lock:
        _counters[key] += amount


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


def _counter_lines() -> list[str]:
    with _lock:
        items = sorted(_counters.items())
    lines: list[str] = []
    seen: set[str] = set()
    for (name, labels), value in items:
        if name not in seen:
            lines.append(f"# TYPE {name} counter")
            seen.add(name)
        lines.append(f"{name}{_format_labels(labels)} {value}")
    return lines


def _db_gauge_lines() -> list[str]:
    settings = get_settings()
    if not get_neon_dsn(settings):
        return ["db_metrics_available 0"]
    lines: list[str] = ["db_metrics_available 1"]
    try:
        with neon_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
            cur.execute("select status, count(*)::int from platform.pipeline_runs group by status")
            lines.append("# TYPE pipeline_runs gauge")
            for status, count in cur.fetchall():
                lines.append(f'pipeline_runs{{status="{status}"}} {count}')

            cur.execute(
                "select status, count(*)::int from rcm.eligibility_requests group by status"
            )
            lines.append("# TYPE eligibility_requests gauge")
            for status, count in cur.fetchall():
                lines.append(f'eligibility_requests{{status="{status}"}} {count}')

            cur.execute(
                """
                    select coalesce(health_status, 'unknown'), count(*)::int
                    from rcm.opendental_connections
                    group by 1
                    """
            )
            lines.append("# TYPE opendental_connections gauge")
            for status, count in cur.fetchall():
                lines.append(f'opendental_connections{{health="{status}"}} {count}')
    except Exception as exc:
        logger.warning("metrics DB gauges failed: %s: %s", type(exc).__name__, exc)
        lines.append("db_metrics_error 1")
    return lines


def _coding_gauge_lines() -> list[str]:
    """Coding-agent accuracy gauges from analytics.coding_scorecard (last 30d)."""
    settings = get_settings()
    if not get_neon_dsn(settings):
        return []
    try:
        with neon_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select
                  coalesce(sum(lines_total), 0),
                  coalesce(sum(lines_proposed), 0),
                  coalesce(sum(lines_decided), 0),
                  coalesce(sum(top1_hits), 0),
                  coalesce(sum(lines_with_gap), 0),
                  coalesce(sum(false_gaps), 0),
                  coalesce(sum(lines_in_needs_info), 0)
                from analytics.coding_scorecard
                where run_date >= (current_date - 30)
                """
            )
            row = cur.fetchone() or (0, 0, 0, 0, 0, 0, 0)
    except Exception as exc:
        logger.warning("coding metrics gauges failed: %s: %s", type(exc).__name__, exc)
        return []
    total, proposed, decided, hits, gapped, false_gaps, needs_info = (int(v) for v in row)

    def ratio(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return [
        "# TYPE coding_lines_total gauge",
        f"coding_lines_total {total}",
        "# TYPE coding_top1_accuracy gauge",
        f"coding_top1_accuracy {ratio(hits, decided)}",
        "# TYPE coding_coverage gauge",
        f"coding_coverage {ratio(proposed, total)}",
        "# TYPE coding_needs_info_rate gauge",
        f"coding_needs_info_rate {ratio(needs_info, total)}",
        "# TYPE coding_false_gap_rate gauge",
        f"coding_false_gap_rate {ratio(false_gaps, gapped)}",
    ]


def _sse_gauge_lines() -> list[str]:
    try:
        from app.realtime.bus import bus

        return [
            "# TYPE realtime_sse_subscribers gauge",
            f"realtime_sse_subscribers {bus.subscriber_count()}",
        ]
    except Exception:
        return []


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    lines = _db_gauge_lines() + _coding_gauge_lines() + _sse_gauge_lines() + _counter_lines()
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
