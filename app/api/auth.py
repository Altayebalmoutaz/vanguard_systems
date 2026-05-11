"""
Authentication dependencies for the main FastAPI app.

Two accepted credentials:

1. **Supabase JWT** in ``Authorization: Bearer <token>``. Verified with HS256 and
   the project's JWT secret (``Settings.supabase_jwt_secret``). Returns the decoded
   claims as the ``Principal``.
2. **Static API key** in ``X-API-Key``. Matched against
   ``Settings.internal_api_keys_set``. Used for trusted server-to-server callers
   (the Supabase edge function ``process-eligibility-request``, ops scripts, etc.).

When ``Settings.require_auth`` is falsy the dependency short-circuits and yields a
synthetic anonymous principal — this preserves the previous open-by-default
behaviour for tests and local dev without requiring per-route changes.

Public routes that should always be reachable (``/health``, ``/`` ping) opt out by
not declaring this dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """Authenticated caller. ``kind`` is ``"jwt"``, ``"api_key"``, or ``"anonymous"``."""

    kind: str
    subject: str
    claims: dict[str, Any]

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"


def _verify_supabase_jwt(token: str, secret: str) -> dict[str, Any]:
    """
    Verify an HS256 Supabase JWT. We import jwt lazily so the dependency stays optional
    (``PyJWT`` is pulled in transitively by ``supabase``); if it's missing we fail closed.
    """
    try:
        import jwt  # type: ignore[import-not-found]
        from jwt import InvalidTokenError  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth_not_configured",
        ) from e

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


async def require_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    """FastAPI dependency: resolve and require an authenticated principal."""
    if not settings.require_auth:
        return Principal(kind="anonymous", subject="anonymous", claims={})

    if x_api_key:
        if x_api_key in settings.internal_api_keys_set:
            return Principal(kind="api_key", subject="internal", claims={})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_api_key",
        )

    if authorization and authorization.lower().startswith("bearer "):
        if not settings.supabase_jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="auth_not_configured",
            )
        token = authorization.split(" ", 1)[1].strip()
        claims = _verify_supabase_jwt(token, settings.supabase_jwt_secret)
        sub = str(claims.get("sub") or claims.get("user_id") or "unknown")
        return Principal(kind="jwt", subject=sub, claims=claims)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="auth_required",
        headers={"WWW-Authenticate": "Bearer"},
    )


PrincipalDep = Annotated[Principal, Depends(require_principal)]
