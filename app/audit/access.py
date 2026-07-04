"""Audit helpers for PHI read access."""

from __future__ import annotations

from uuid import UUID

from app.audit.writer import write_audit_log
from app.config import Settings


def audit_phi_read(
    settings: Settings,
    *,
    practice_id: str,
    action: str,
    entity_type: str,
    entity_id: UUID | str | None,
    performed_by: str,
) -> None:
    entity_uuid = UUID(str(entity_id)) if entity_id else None
    write_audit_log(
        settings,
        practice_id=practice_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_uuid,
        performed_by=performed_by,
        metadata={},
    )
