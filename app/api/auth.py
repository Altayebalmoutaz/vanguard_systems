"""
Authentication dependencies for the main FastAPI app.

Two accepted credentials:

1. **Supabase JWT** in ``Authorization: Bearer <token>``. Verified in two ways:

   * **Asymmetric (preferred)** — staff access tokens minted by Supabase Auth are
     signed with the project's asymmetric signing keys (ES256/RS256). They are
     verified against the project JWKS
     (``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``).
   * **Legacy HS256 (fallback)** — the project's legacy shared JWT secret
     (``Settings.supabase_jwt_secret``) still signs the static ``anon`` /
     ``service_role`` keys. Used when the asymmetric path does not apply.

   The decoded claims become the ``Principal``.
2. **Static API key** in ``X-API-Key``. Matched against
   ``Settings.internal_api_keys_set``. Used for trusted server-to-server callers
   (the Supabase edge function ``process-eligibility-request``, ops scripts, etc.).

**Fail-open ordering matters:** when a Bearer token is present but cannot be
verified (wrong/rotated key, JWKS unreachable), the dependency does **not** hard
fail with 401. It falls through to the ``X-API-Key`` path so a mismatched or
rotated signing key never takes the whole dashboard down. A ``401 invalid_token``
is only returned when the Bearer fails verification *and* no valid API key was
supplied.

When ``Settings.require_auth`` is falsy the dependency short-circuits and yields a
synthetic anonymous principal — this preserves the previous open-by-default
behaviour for tests and local dev without requiring per-route changes.

Public routes that should always be reachable (``/health``, ``/`` ping) opt out by
not declaring this dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import InvalidTokenError, PyJWKClient
from jwt.exceptions import PyJWKClientError

from app.api.rbac import (
    PracticeRole,
    RbacNotConfiguredError,
    RbacResolutionError,
    has_any_role,
    resolve_practice_roles,
)
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Supabase Auth signs staff access tokens with EC (ES256) keys; RS256 is accepted
# for forward-compatibility with projects that rotate to RSA keys.
_ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]
# Keep JWKS lookups snappy so a slow/unreachable auth host degrades to the API-key
# fallback instead of stalling every authenticated request.
_JWKS_TIMEOUT_SECONDS = 5.0

# PyJWKClient caches fetched signing keys internally; cache one client per JWKS URL
# so we reuse that key cache across requests instead of refetching every call.
_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller. ``kind`` is ``"jwt"``, ``"api_key"``, or ``"anonymous"``."""

    kind: str
    subject: str
    claims: dict[str, Any]
    practice_roles: tuple[PracticeRole, ...] = ()

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"

    @property
    def practice_ids(self) -> tuple[str, ...]:
        return tuple(role.practice_id for role in self.practice_roles)

    def has_any_role(self, *roles: str) -> bool:
        return self.kind == "api_key" or has_any_role(self.practice_roles, set(roles))


def _verify_supabase_jwt(token: str, secret: str) -> dict[str, Any]:
    """Verify a legacy HS256 Supabase JWT, raising ``InvalidTokenError`` on failure."""
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )


def _jwks_url(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _jwks_client(jwks_url: str) -> PyJWKClient:
    client = _JWKS_CLIENTS.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url, cache_keys=True, timeout=_JWKS_TIMEOUT_SECONDS)
        _JWKS_CLIENTS[jwks_url] = client
    return client


def _verify_via_jwks(token: str, supabase_url: str) -> dict[str, Any]:
    """Verify an asymmetric (ES256/RS256) Supabase access token against the project JWKS."""
    signing_key = _jwks_client(_jwks_url(supabase_url)).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=_ASYMMETRIC_ALGORITHMS,
        options={"verify_aud": False},
    )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    return None


def _bearer_verification_configured(settings: Settings) -> bool:
    """True when at least one verification method (JWKS or legacy secret) is available."""
    return bool(settings.supabase_url or settings.supabase_jwt_secret)


def _verify_bearer(settings: Settings, token: str) -> dict[str, Any] | None:
    """
    Verify a Bearer token, returning claims on success or ``None`` on failure.

    Tries the asymmetric JWKS path first (Supabase's current staff tokens), then the
    legacy HS256 secret. Verification/transport failures return ``None`` so the caller
    can fall through to the API-key path rather than take the dashboard down.
    """
    if settings.supabase_url:
        try:
            return _verify_via_jwks(token, settings.supabase_url)
        except InvalidTokenError:
            logger.info("Bearer token failed JWKS (asymmetric) verification.")
        except PyJWKClientError:
            logger.warning("Supabase JWKS lookup failed; falling back to legacy secret.")
        except Exception:  # noqa: BLE001 - resilience: JWKS transport must not 500 the app
            logger.warning("Unexpected error verifying Bearer via JWKS.", exc_info=True)

    if settings.supabase_jwt_secret:
        try:
            return _verify_supabase_jwt(token, settings.supabase_jwt_secret)
        except InvalidTokenError:
            logger.info("Bearer token failed legacy HS256 verification.")

    return None


def _principal_from_claims(settings: Settings, claims: dict[str, Any]) -> Principal:
    """Build a JWT principal from verified claims, resolving RBAC practice roles."""
    sub = str(claims.get("sub") or claims.get("user_id") or "unknown")
    try:
        practice_roles = resolve_practice_roles(settings, user_id=sub, claims=claims)
    except RbacNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rbac_not_configured",
        ) from e
    except RbacResolutionError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rbac_unavailable",
        ) from e
    if settings.require_rbac and not practice_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="role_required",
        )
    return Principal(kind="jwt", subject=sub, claims=claims, practice_roles=practice_roles)


async def require_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    """FastAPI dependency: resolve and require an authenticated principal."""
    if not settings.require_auth:
        return Principal(kind="anonymous", subject="anonymous", claims={})

    token = _bearer_token(authorization)
    bearer_verify_failed = False
    if token and _bearer_verification_configured(settings):
        claims = _verify_bearer(settings, token)
        if claims is not None:
            return _principal_from_claims(settings, claims)
        # Verification failed with a real method configured: fall through to the
        # API-key path so a rotated/mismatched signing key can't cause an outage.
        bearer_verify_failed = True

    if x_api_key:
        if x_api_key in settings.internal_api_keys_set:
            return Principal(kind="api_key", subject="internal", claims={})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_api_key",
        )

    if bearer_verify_failed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token:
        # A Bearer was supplied but no verification method is configured.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth_not_configured",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="auth_required",
        headers={"WWW-Authenticate": "Bearer"},
    )


PrincipalDep = Annotated[Principal, Depends(require_principal)]
