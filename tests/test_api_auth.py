"""
Coverage for :mod:`app.api.auth` — Supabase JWT + static API-key auth.

These tests build a fresh ``Settings`` per case and inject it through the FastAPI
dependency-override mechanism, so toggling ``require_auth`` does not pollute the
``get_settings`` LRU cache used by other tests.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import Principal, _verify_supabase_jwt, require_principal
from app.api.rbac import PracticeRole
from app.config import Settings, get_settings


def _settings(**overrides: Any) -> Settings:
    """Build Settings that cannot inherit a real SUPABASE_URL / DATABASE_URL from .env."""
    isolated: dict[str, Any] = {
        "supabase_url": None,
        "supabase_db_password": None,
        "supabase_pooler_host": None,
        "neon_database_url": "",
    }
    isolated.update(overrides)
    return Settings(**isolated)


def _build_app(settings: Settings) -> TestClient:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal: Principal = Depends(require_principal)) -> dict:
        return {
            "kind": principal.kind,
            "subject": principal.subject,
            "practice_roles": [
                {"practice_id": role.practice_id, "role": role.role}
                for role in principal.practice_roles
            ],
        }

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


class AuthDisabledMode(unittest.TestCase):
    def test_no_credentials_returns_anonymous(self) -> None:
        client = _build_app(_settings(require_auth=False))
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"kind": "anonymous", "subject": "anonymous", "practice_roles": []},
        )


class ApiKeyAuth(unittest.TestCase):
    def test_valid_api_key_accepted(self) -> None:
        client = _build_app(
            _settings(
                require_auth=True,
                internal_api_keys="ops-key-1, deploy-key-2",
            )
        )
        resp = client.get("/whoami", headers={"X-API-Key": "deploy-key-2"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "api_key")

    def test_invalid_api_key_rejected_with_401(self) -> None:
        client = _build_app(_settings(require_auth=True, internal_api_keys="ops-key-1"))
        resp = client.get("/whoami", headers={"X-API-Key": "wrong"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_api_key")

    def test_bearer_jwt_wins_over_api_key(self) -> None:
        secret = "super-secret-key-that-is-32-bytes-long-aaaaaaaa"
        good_token = jwt.encode({"sub": "u-1"}, secret, algorithm="HS256")
        client = _build_app(
            _settings(
                require_auth=True,
                supabase_jwt_secret=secret,
                internal_api_keys="ops-key-1",
            )
        )
        resp = client.get(
            "/whoami",
            headers={"X-API-Key": "ops-key-1", "Authorization": f"Bearer {good_token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "jwt")
        self.assertEqual(resp.json()["subject"], "u-1")


class JwtAuth(unittest.TestCase):
    def setUp(self) -> None:
        # PyJWT >= 2.10 emits InsecureKeyLengthWarning for HS256 secrets shorter
        # than 32 bytes; use a long ASCII secret to keep test output quiet.
        self.secret = "jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_valid_jwt_returns_principal(self) -> None:
        token = jwt.encode({"sub": "user-42"}, self.secret, algorithm="HS256")
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["kind"], "jwt")
        self.assertEqual(body["subject"], "user-42")

    def test_jwt_with_user_id_claim_when_no_sub(self) -> None:
        token = jwt.encode({"user_id": "uid-9"}, self.secret, algorithm="HS256")
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["subject"], "uid-9")

    def test_jwt_metadata_roles_used_when_rbac_not_required(self) -> None:
        token = jwt.encode(
            {
                "sub": "user-42",
                "app_metadata": {
                    "practice_roles": {"practice-a": "admin", "practice-b": "read_only"}
                },
            },
            self.secret,
            algorithm="HS256",
        )
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["practice_roles"],
            [
                {"practice_id": "practice-a", "role": "admin"},
                {"practice_id": "practice-b", "role": "read_only"},
            ],
        )

    def test_required_rbac_without_neon_returns_503(self) -> None:
        token = jwt.encode({"sub": "user-42"}, self.secret, algorithm="HS256")
        client = _build_app(
            _settings(
                require_auth=True,
                require_rbac=True,
                supabase_jwt_secret=self.secret,
                neon_database_url="",
            )
        )
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "rbac_not_configured")

    def test_invalid_jwt_signature_rejected(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "different-secret-aaaaaaaaaaaaaaaaaaaaaaa",
            algorithm="HS256",
        )
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_token")

    def test_malformed_bearer_rejected(self) -> None:
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": "Bearer not.a.real.jwt"})
        self.assertEqual(resp.status_code, 401)

    def test_missing_jwt_secret_falls_back_to_api_key(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "any-secret-padded-out-to-32-bytes-aaaa",
            algorithm="HS256",
        )
        client = _build_app(
            _settings(
                require_auth=True,
                supabase_jwt_secret=None,
                internal_api_keys="ops-key-1",
            )
        )
        resp = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {token}", "X-API-Key": "ops-key-1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "api_key")

    def test_missing_jwt_secret_without_api_key_returns_auth_not_configured(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "any-secret-padded-out-to-32-bytes-aaaa",
            algorithm="HS256",
        )
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=None))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "auth_not_configured")


class BearerFallThrough(unittest.TestCase):
    """A Bearer that fails verification must not take the app down when an API key is valid."""

    def setUp(self) -> None:
        self.secret = "jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_invalid_bearer_falls_back_to_api_key(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "rotated-out-secret-padded-to-32-bytes-aaaa",
            algorithm="HS256",
        )
        client = _build_app(
            _settings(
                require_auth=True,
                supabase_jwt_secret=self.secret,
                internal_api_keys="ops-key-1",
            )
        )
        resp = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {token}", "X-API-Key": "ops-key-1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "api_key")

    def test_invalid_bearer_without_api_key_returns_401(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "rotated-out-secret-padded-to-32-bytes-aaaa",
            algorithm="HS256",
        )
        client = _build_app(_settings(require_auth=True, supabase_jwt_secret=self.secret))
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_token")


class _FakeSigningKey:
    def __init__(self, key: object) -> None:
        self.key = key


class _FakeJwksClient:
    """Stand-in for ``PyJWKClient`` that returns a fixed public key (no network)."""

    def __init__(self, public_key: object) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> _FakeSigningKey:
        return _FakeSigningKey(self._public_key)


class JwksAuth(unittest.TestCase):
    """Asymmetric (ES256) staff tokens verify against the project JWKS."""

    def setUp(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = self.private_key.public_key()

    def test_es256_bearer_verifies_via_jwks(self) -> None:
        token = jwt.encode({"sub": "auth-user-1"}, self.private_key, algorithm="ES256")
        client = _build_app(
            _settings(require_auth=True, supabase_url="https://project.supabase.co")
        )
        with patch(
            "app.api.auth._jwks_client",
            return_value=_FakeJwksClient(self.public_key),
        ):
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["kind"], "jwt")
        self.assertEqual(body["subject"], "auth-user-1")

    def test_jwks_failure_falls_back_to_legacy_secret(self) -> None:
        secret = "jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        token = jwt.encode({"sub": "legacy-user"}, secret, algorithm="HS256")
        client = _build_app(
            _settings(
                require_auth=True,
                supabase_url="https://project.supabase.co",
                supabase_jwt_secret=secret,
            )
        )
        # HS256 token can't verify via JWKS; the fake client's key rejects it,
        # so verification must fall back to the legacy secret.
        with patch(
            "app.api.auth._jwks_client",
            return_value=_FakeJwksClient(self.public_key),
        ):
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["subject"], "legacy-user")

    def test_jwks_failure_falls_back_to_api_key(self) -> None:
        other_key = ec.generate_private_key(ec.SECP256R1())
        token = jwt.encode({"sub": "auth-user-1"}, other_key, algorithm="ES256")
        client = _build_app(
            _settings(
                require_auth=True,
                supabase_url="https://project.supabase.co",
                supabase_jwt_secret="jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                internal_api_keys="ops-key-1",
            )
        )
        with patch(
            "app.api.auth._jwks_client",
            return_value=_FakeJwksClient(self.public_key),
        ):
            resp = client.get(
                "/whoami",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-API-Key": "ops-key-1",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "api_key")


class NoCredentials(unittest.TestCase):
    def test_no_creds_with_auth_required_returns_401(self) -> None:
        client = _build_app(_settings(require_auth=True))
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "auth_required")
        self.assertEqual(resp.headers.get("WWW-Authenticate"), "Bearer")

    def test_non_bearer_authorization_returns_401(self) -> None:
        client = _build_app(_settings(require_auth=True))
        resp = client.get("/whoami", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "auth_required")


class VerifyJwtHelper(unittest.TestCase):
    def test_returns_decoded_claims(self) -> None:
        secret = "verify-secret-padded-to-32-bytes-aaaaaaaa"
        token = jwt.encode({"sub": "abc", "role": "biller"}, secret, algorithm="HS256")
        claims = _verify_supabase_jwt(token, secret)
        self.assertEqual(claims["sub"], "abc")
        self.assertEqual(claims["role"], "biller")


class PrincipalDataclass(unittest.TestCase):
    def test_is_anonymous_property(self) -> None:
        anon = Principal(kind="anonymous", subject="anonymous", claims={})
        signed = Principal(kind="jwt", subject="u", claims={"sub": "u"})
        self.assertTrue(anon.is_anonymous)
        self.assertFalse(signed.is_anonymous)

    def test_practice_ids_and_role_helper(self) -> None:
        principal = Principal(
            kind="jwt",
            subject="u",
            claims={"sub": "u"},
            practice_roles=(PracticeRole(practice_id="p1", role="billing_lead"),),
        )
        self.assertEqual(principal.practice_ids, ("p1",))
        self.assertTrue(principal.has_any_role("admin", "billing_lead"))
        self.assertFalse(principal.has_any_role("front_office"))


if __name__ == "__main__":
    unittest.main()
