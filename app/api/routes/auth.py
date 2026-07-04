"""Authenticated staff identity and RBAC introspection routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.auth import PrincipalDep

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def get_current_principal(principal: PrincipalDep) -> dict:
    """Return the authenticated caller and resolved practice roles."""
    return {
        "kind": principal.kind,
        "subject": principal.subject,
        "practice_roles": [
            {"practice_id": role.practice_id, "role": role.role}
            for role in principal.practice_roles
        ],
    }
