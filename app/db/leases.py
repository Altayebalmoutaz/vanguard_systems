"""Postgres advisory-lock leases for single-flight background workers.

The pipeline queue is already multi-replica safe (``FOR UPDATE SKIP LOCKED``), but
the OD poller / retry / voice sweeps would duplicate work if several app replicas
ran them concurrently. Each sweep takes a session-scoped advisory lock for its
duration; replicas that fail to get the lock skip that pass.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

import psycopg

from app.config import Settings
from app.db.connection import get_neon_dsn

logger = logging.getLogger(__name__)

LEASE_OD_POLLER = "worker:opendental_poller"
LEASE_RETRY_WORKER = "worker:eligibility_retry"
LEASE_VOICE_WORKER = "worker:voice_verification"


@contextmanager
def try_lease(settings: Settings, lease_name: str) -> Generator[bool, None, None]:
    """Yield True when this process holds the named lease for the block's duration.

    Without a configured database there is nothing to coordinate through, so the
    lease is granted (single-instance dev mode). Session-level advisory locks are
    released automatically when the connection closes.
    """
    dsn = get_neon_dsn(settings)
    if not dsn:
        yield True
        return
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select pg_try_advisory_lock(hashtext(%s)::bigint)", (lease_name,)
                )
                row = cur.fetchone()
            acquired = bool(row and row[0])
            if not acquired:
                logger.debug("lease %s held elsewhere; skipping pass", lease_name)
            yield acquired
    except psycopg.Error as exc:
        logger.warning("lease %s unavailable (%s); skipping pass", lease_name, exc)
        yield False
