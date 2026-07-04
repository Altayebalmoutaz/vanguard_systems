"""Fail-closed guards for the Neon PHI plane."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.db.connection import get_neon_dsn


class PhiStoreError(RuntimeError):
    """Raised when PHI must be stored on Neon but prerequisites are missing."""


def neon_phi_configured(settings: Settings | None = None) -> bool:
    return bool(get_neon_dsn(settings or get_settings()))


def require_practice_id_for_neon(
    practice_id: str | None,
    *,
    row: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """Return a non-empty practice_id or raise when Neon PHI is active."""
    pid = (practice_id or "").strip()
    if not pid and row is not None:
        pid = str(row.get("practice_id") or "").strip()
    if neon_phi_configured(settings) and not pid:
        raise PhiStoreError("practice_id required for Neon PHI store")
    return pid
