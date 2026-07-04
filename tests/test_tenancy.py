"""Tenant context resolution policy."""

from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from app.api.auth import Principal
from app.api.rbac import PracticeRole
from app.api.tenancy import require_practice_context
from app.config import Settings


def _run(coro):
    return asyncio.run(coro)


class TenantContextPolicyTests(unittest.TestCase):
    def test_jwt_with_one_role_auto_selects_practice(self) -> None:
        principal = Principal(
            kind="jwt",
            subject="u",
            claims={},
            practice_roles=(PracticeRole(practice_id="p1", role="billing_lead"),),
        )
        ctx = _run(require_practice_context(principal, Settings(require_auth=True)))
        self.assertEqual(ctx.practice_id, "p1")
        self.assertEqual(ctx.role, "billing_lead")

    def test_jwt_with_multiple_roles_requires_header_when_rbac_required(self) -> None:
        principal = Principal(
            kind="jwt",
            subject="u",
            claims={},
            practice_roles=(
                PracticeRole(practice_id="p1", role="admin"),
                PracticeRole(practice_id="p2", role="read_only"),
            ),
        )
        with self.assertRaises(HTTPException) as ctx:
            _run(
                require_practice_context(
                    principal,
                    Settings(require_auth=True, require_rbac=True),
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "practice_required")

    def test_jwt_header_must_match_allowed_practice(self) -> None:
        principal = Principal(
            kind="jwt",
            subject="u",
            claims={},
            practice_roles=(PracticeRole(practice_id="p1", role="front_office"),),
        )
        with self.assertRaises(HTTPException) as ctx:
            _run(
                require_practice_context(
                    principal,
                    Settings(require_auth=True, require_rbac=True),
                    x_practice_id="p2",
                )
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "practice_forbidden")

    def test_api_key_requires_explicit_practice(self) -> None:
        principal = Principal(kind="api_key", subject="internal", claims={})
        with self.assertRaises(HTTPException) as ctx:
            _run(require_practice_context(principal, Settings(require_auth=True)))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "practice_required")

        resolved = _run(
            require_practice_context(
                principal,
                Settings(require_auth=True),
                x_practice_id="p1",
            )
        )
        self.assertEqual(resolved.practice_id, "p1")


if __name__ == "__main__":
    unittest.main()
