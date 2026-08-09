"""Autonomy tiers: decide how much a suggested line can be trusted.

Combines calibrated confidence, deterministic validation (valid code + no
blocking gap), stakes, and an empirical per-code allowlist derived from prior
dentist decisions. The output drives the scribe UI: ``auto`` lines can be
one-click accepted, ``review`` lines get a quick confirm, ``ask`` lines must be
resolved first.
"""

from __future__ import annotations

import logging

from app.coding.cache import cached
from app.coding.config import CodingSettings
from app.coding.reliability import is_high_stakes
from app.coding.schemas import AutonomyTier
from app.config import Settings
from app.db.connection import database_connection, get_neon_dsn
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)


def _allowlist_query_key(practice_id: str, cfg: CodingSettings) -> str:
    return (
        f"autonomy_allowlist:{practice_id}:{cfg.coding_autonomy_allowlist_min_decisions}"
        f":{cfg.coding_autonomy_allowlist_min_hit_rate}"
    )


def fetch_autonomy_allowlist(
    settings: Settings,
    *,
    practice_id: str,
    cfg: CodingSettings,
    ttl_seconds: float = 600.0,
) -> frozenset[str]:
    """CDT codes with enough decisions and a high enough top-1 approval rate.

    Empty set when no DB / insufficient history (so nothing is auto-approved on
    stakes it hasn't earned).
    """
    if not cfg.coding_autonomy_enabled or not get_neon_dsn(settings):
        return frozenset()

    def _load() -> frozenset[str]:
        try:
            with (
                database_connection(settings, bypass_rls=True) as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    select suggested_cdt
                    from analytics.coding_line_outcomes
                    where practice_id = %s
                      and suggested_cdt <> ''
                      and decision_action is not null
                    group by suggested_cdt
                    having count(*) >= %s
                       and avg(
                             case when decision_action = 'approved'
                                    or (final_cdt <> '' and final_cdt = suggested_cdt)
                                  then 1.0 else 0.0 end
                           ) >= %s
                    """,
                    (
                        practice_id,
                        cfg.coding_autonomy_allowlist_min_decisions,
                        cfg.coding_autonomy_allowlist_min_hit_rate,
                    ),
                )
                return frozenset(str(r[0]).upper().strip() for r in cur.fetchall())
        except Exception as exc:
            logger.warning("autonomy allowlist load failed: %s", scrub_for_log(str(exc)))
            return frozenset()

    return cached(_allowlist_query_key(practice_id, cfg), ttl_seconds, _load)


def decide_tier(
    *,
    cdt_code: str | None,
    calibrated_confidence: float,
    has_blocking_gap: bool,
    is_valid: bool,
    payer_conflict: bool,
    cfg: CodingSettings,
    allowlist: frozenset[str] = frozenset(),
) -> AutonomyTier:
    """Map validation + calibrated confidence + stakes to an autonomy tier."""
    if not cfg.coding_autonomy_enabled:
        return AutonomyTier.review
    code = (cdt_code or "").upper().strip()
    if not code or not is_valid or has_blocking_gap:
        return AutonomyTier.ask

    conf = max(0.0, min(1.0, calibrated_confidence))
    if conf < cfg.coding_autonomy_review_threshold:
        return AutonomyTier.ask

    high_stakes = is_high_stakes(code, cfg)
    allowlisted = code in allowlist
    qualifies_auto = (
        conf >= cfg.coding_autonomy_auto_threshold
        and not payer_conflict
        and (not high_stakes or allowlisted)
    )
    return AutonomyTier.auto if qualifies_auto else AutonomyTier.review
