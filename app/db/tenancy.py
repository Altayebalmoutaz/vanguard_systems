"""Helpers for applying tenant context to direct Postgres sessions."""

from __future__ import annotations

from typing import Protocol


class TenantCursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...


def set_tenant_gucs(
    cursor: TenantCursor,
    *,
    practice_id: str,
    bypass_rls: bool = False,
) -> None:
    """Set Neon RLS GUCs for the remainder of the database session."""
    # is_local=false → session-scoped (one connection per request/work unit).
    cursor.execute("select set_config('app.practice_id', %s, false)", (practice_id,))
    cursor.execute(
        "select set_config('app.bypass_rls', %s, false)",
        ("true" if bypass_rls else "false",),
    )
