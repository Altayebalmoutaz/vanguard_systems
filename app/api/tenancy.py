"""Request-level tenant selection and authorization.

Every PHI-facing route should resolve a ``PracticeContext`` before doing work.
JWT callers are constrained to the practices/roles resolved by RBAC; API-key
callers must provide ``X-Practice-ID`` explicitly for tenant-scoped operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.api.auth import Principal, PrincipalDep
from app.api.rbac import PracticeRoleName
from app.config import Settings, get_settings


@dataclass(frozen=True)
class PracticeContext:
    practice_id: str
    role: PracticeRoleName | None
    principal: Principal


def _normalize_practice_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


async def require_practice_context(
    principal: PrincipalDep,
    settings: Annotated[Settings, Depends(get_settings)],
    x_practice_id: Annotated[str | None, Header(alias="X-Practice-ID")] = None,
) -> PracticeContext:
    """Resolve and authorize the active practice for this request."""
    requested = _normalize_practice_id(x_practice_id)

    if principal.kind == "anonymous":
        # Auth-off local mode: preserve old behavior but carry a deterministic tenant.
        return PracticeContext(
            practice_id=requested or "local-dev",
            role="admin",
            principal=principal,
        )

    if principal.kind == "api_key":
        if not requested:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="practice_required",
            )
        return PracticeContext(practice_id=requested, role="admin", principal=principal)

    roles_by_practice = {role.practice_id: role.role for role in principal.practice_roles}
    if requested:
        role = roles_by_practice.get(requested)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="practice_forbidden",
            )
        return PracticeContext(practice_id=requested, role=role, principal=principal)

    if len(principal.practice_roles) == 1:
        only = principal.practice_roles[0]
        return PracticeContext(practice_id=only.practice_id, role=only.role, principal=principal)

    if settings.require_rbac:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="practice_required",
        )

    # RBAC preview/local mode with no claims: keep route behavior available.
    return PracticeContext(practice_id="local-dev", role=None, principal=principal)


PracticeContextDep = Annotated[PracticeContext, Depends(require_practice_context)]
