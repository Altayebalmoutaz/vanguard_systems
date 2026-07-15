"""Role resolution for authenticated staff users.

Source of truth is the Neon PHI-plane table ``platform.user_practice_roles``.
During local development, callers can leave ``REQUIRE_RBAC=0`` and optionally
carry lightweight role metadata in the Supabase JWT for UI/demo flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg

from app.config import Settings
from app.db.connection import neon_connection

logger = logging.getLogger(__name__)

PracticeRoleName = Literal["admin", "billing_lead", "front_office", "read_only"]
VALID_ROLE_NAMES: frozenset[str] = frozenset({"admin", "billing_lead", "front_office", "read_only"})


class RbacNotConfiguredError(RuntimeError):
    """RBAC was required but no role source was configured."""


class RbacResolutionError(RuntimeError):
    """RBAC was configured but roles could not be resolved safely."""


@dataclass(frozen=True)
class PracticeRole:
    practice_id: str
    role: PracticeRoleName


def _coerce_role(value: Any) -> PracticeRoleName | None:
    role = str(value or "").strip()
    if role in VALID_ROLE_NAMES:
        return role  # type: ignore[return-value]
    return None


def _claim_roles(claims: dict[str, Any]) -> tuple[PracticeRole, ...]:
    """Best-effort dev fallback for custom Supabase JWT app_metadata."""
    metadata = claims.get("app_metadata")
    if not isinstance(metadata, dict):
        metadata = claims

    practice_id = str(metadata.get("practice_id") or metadata.get("practice") or "").strip()
    role = _coerce_role(metadata.get("role") or metadata.get("practice_role"))
    if practice_id and role:
        return (PracticeRole(practice_id=practice_id, role=role),)

    raw_roles = metadata.get("practice_roles")
    out: list[PracticeRole] = []
    if isinstance(raw_roles, dict):
        for pid, raw_role in raw_roles.items():
            coerced = _coerce_role(raw_role)
            if coerced and str(pid).strip():
                out.append(PracticeRole(practice_id=str(pid).strip(), role=coerced))
    elif isinstance(raw_roles, list):
        for item in raw_roles:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("practice_id") or "").strip()
            coerced = _coerce_role(item.get("role"))
            if pid and coerced:
                out.append(PracticeRole(practice_id=pid, role=coerced))
    return tuple(out)


def _fetch_neon_roles(settings: Settings, user_id: str) -> tuple[PracticeRole, ...]:
    try:
        user_uuid = UUID(user_id)
    except ValueError as exc:
        raise RbacResolutionError("Supabase subject is not a UUID") from exc

    if not settings.neon_database_url:
        raise RbacNotConfiguredError("NEON_DATABASE_URL is not configured")

    try:
        with neon_connection(settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    select practice_id, role
                    from platform.user_practice_roles
                    where user_id = %s
                    order by practice_id
                    """,
                (user_uuid,),
            )
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise RbacResolutionError("Failed to resolve practice roles") from exc

    roles: list[PracticeRole] = []
    for practice_id, raw_role in rows:
        role = _coerce_role(raw_role)
        if role:
            roles.append(PracticeRole(practice_id=str(practice_id), role=role))
    return tuple(roles)


def resolve_practice_roles(
    settings: Settings,
    *,
    user_id: str,
    claims: dict[str, Any],
) -> tuple[PracticeRole, ...]:
    """Resolve staff practice roles, using Neon when RBAC is required."""
    if settings.require_rbac:
        if not settings.neon_database_url:
            raise RbacNotConfiguredError("REQUIRE_RBAC=1 requires NEON_DATABASE_URL")
        return _fetch_neon_roles(settings, user_id)

    roles = _claim_roles(claims)
    if roles:
        logger.info(
            "Resolved practice roles from JWT metadata because Neon RBAC is not configured."
        )
    return roles


def has_any_role(roles: tuple[PracticeRole, ...], allowed: set[str]) -> bool:
    return any(role.role in allowed for role in roles)
