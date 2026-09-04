from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.api.rbac import resolve_practice_roles
from app.config import Settings

_USER_ID = "bc6f84da-ae62-4c67-9d8c-d12df3e06fcc"


def test_resolve_practice_roles_bypasses_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def fake_conn(_settings: Settings, **kwargs: object):
        captured.update(kwargs)
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("partner_clinic", "admin"),
            ("vgd_mock_brooklyn", "admin"),
        ]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        yield conn

    monkeypatch.setattr("app.api.rbac.neon_connection", fake_conn)
    roles = resolve_practice_roles(
        Settings(require_rbac=True, neon_database_url="postgresql://test"),
        user_id=_USER_ID,
        claims={},
    )
    assert captured.get("bypass_rls") is True
    assert [role.practice_id for role in roles] == [
        "partner_clinic",
        "vgd_mock_brooklyn",
    ]
