"""
Coverage for :mod:`app.api.auth` — Supabase JWT + static API-key auth.

These tests build a fresh ``Settings`` per case and inject it through the FastAPI
dependency-override mechanism, so toggling ``require_auth`` does not pollute the
``get_settings`` LRU cache used by other tests.
"""

from __future__ import annotations

import unittest

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import Principal, _verify_supabase_jwt, require_principal
from app.config import Settings, get_settings


def _build_app(settings: Settings) -> TestClient:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal: Principal = Depends(require_principal)) -> dict:
        return {"kind": principal.kind, "subject": principal.subject}

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


class AuthDisabledMode(unittest.TestCase):
    def test_no_credentials_returns_anonymous(self) -> None:
        client = _build_app(Settings(require_auth=False))
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"kind": "anonymous", "subject": "anonymous"})


class ApiKeyAuth(unittest.TestCase):
    def test_valid_api_key_accepted(self) -> None:
        client = _build_app(
            Settings(
                require_auth=True,
                internal_api_keys="ops-key-1, deploy-key-2",
            )
        )
        resp = client.get("/whoami", headers={"X-API-Key": "deploy-key-2"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "api_key")

    def test_invalid_api_key_rejected_with_401(self) -> None:
        client = _build_app(
            Settings(require_auth=True, internal_api_keys="ops-key-1")
        )
        resp = client.get("/whoami", headers={"X-API-Key": "wrong"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_api_key")

    def test_api_key_path_takes_precedence_over_jwt(self) -> None:
        secret = "super-secret-key-that-is-32-bytes-long-aaaaaaaa"
        good_token = jwt.encode({"sub": "u-1"}, secret, algorithm="HS256")
        client = _build_app(
            Settings(
                require_auth=True,
                supabase_jwt_secret=secret,
                internal_api_keys="ops-key-1",
            )
        )
        # Wrong api key + valid JWT -> the api-key branch fires first and 401s.
        resp = client.get(
            "/whoami",
            headers={"X-API-Key": "bogus", "Authorization": f"Bearer {good_token}"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_api_key")


class JwtAuth(unittest.TestCase):
    def setUp(self) -> None:
        # PyJWT >= 2.10 emits InsecureKeyLengthWarning for HS256 secrets shorter
        # than 32 bytes; use a long ASCII secret to keep test output quiet.
        self.secret = "jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_valid_jwt_returns_principal(self) -> None:
        token = jwt.encode({"sub": "user-42"}, self.secret, algorithm="HS256")
        client = _build_app(
            Settings(require_auth=True, supabase_jwt_secret=self.secret)
        )
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["kind"], "jwt")
        self.assertEqual(body["subject"], "user-42")

    def test_jwt_with_user_id_claim_when_no_sub(self) -> None:
        token = jwt.encode({"user_id": "uid-9"}, self.secret, algorithm="HS256")
        client = _build_app(
            Settings(require_auth=True, supabase_jwt_secret=self.secret)
        )
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["subject"], "uid-9")

    def test_invalid_jwt_signature_rejected(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "different-secret-aaaaaaaaaaaaaaaaaaaaaaa",
            algorithm="HS256",
        )
        client = _build_app(
            Settings(require_auth=True, supabase_jwt_secret=self.secret)
        )
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "invalid_token")

    def test_malformed_bearer_rejected(self) -> None:
        client = _build_app(
            Settings(require_auth=True, supabase_jwt_secret=self.secret)
        )
        resp = client.get(
            "/whoami", headers={"Authorization": "Bearer not.a.real.jwt"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_missing_jwt_secret_returns_503(self) -> None:
        token = jwt.encode(
            {"sub": "u"},
            "any-secret-padded-out-to-32-bytes-aaaa",
            algorithm="HS256",
        )
        client = _build_app(
            Settings(require_auth=True, supabase_jwt_secret=None)
        )
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "auth_not_configured")


class NoCredentials(unittest.TestCase):
    def test_no_creds_with_auth_required_returns_401(self) -> None:
        client = _build_app(Settings(require_auth=True))
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "auth_required")
        self.assertEqual(resp.headers.get("WWW-Authenticate"), "Bearer")

    def test_non_bearer_authorization_returns_401(self) -> None:
        client = _build_app(Settings(require_auth=True))
        resp = client.get(
            "/whoami", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
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


if __name__ == "__main__":
    unittest.main()
