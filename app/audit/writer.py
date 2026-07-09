"""Unified audit writer for the PHI plane."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.db.json_safe import json_safe

logger = logging.getLogger(__name__)


def write_audit_log(
    settings: Settings,
    *,
    practice_id: str,
    action: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    performed_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one row to ``audit.audit_logs`` (Neon only)."""
    if not get_neon_dsn(settings):
        logger.debug("audit log skipped: Neon not configured")
        return
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into audit.audit_logs (
                      practice_id, entity_type, entity_id, action, performed_by, metadata
                    )
                    values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        practice_id,
                        entity_type,
                        entity_id,
                        action,
                        performed_by,
                        Jsonb(json_safe(metadata or {})),
                    ),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)
