"""Tests for prior-auth agent_runs status transitions and resolve endpoint."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import rcm
from app.api.tenancy import PracticeContext, require_practice_context
from app.config import Settings, get_settings
from app.integrations.agent_runs import (
    AGENT_PRIOR_AUTH,
    AgentRunNotFoundError,
    AgentRunTransitionError,
    update_agent_run_status,
    validate_agent_run_transition,
)


class AgentRunTransitionValidationTests(unittest.TestCase):
    def test_pending_review_can_resolve_to_terminal_states(self) -> None:
        for status in ("approved", "denied", "expired", "superseded"):
            validate_agent_run_transition("pending_review", status)

    def test_terminal_states_cannot_transition(self) -> None:
        for current in ("approved", "denied", "expired", "superseded"):
            with self.subTest(current=current):
                with self.assertRaises(AgentRunTransitionError):
                    validate_agent_run_transition(current, "approved")


class UpdateAgentRunStatusRoutingTests(unittest.TestCase):
    @patch("app.integrations.agent_runs._update_agent_run_status_neon")
    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_update_prefers_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_neon_update: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        run_id = UUID("11111111-1111-1111-1111-111111111111")
        mock_neon_update.return_value = {"id": str(run_id), "status": "approved"}
        settings = Settings(neon_database_url="postgresql://neon")

        row = update_agent_run_status(
            settings,
            run_id,
            "approved",
            practice_id="practice-1",
            meta_patch={"resolve_reason": "staff confirmed"},
        )

        self.assertEqual(row["status"], "approved")
        mock_neon_update.assert_called_once()

    @patch("app.integrations.agent_runs._update_agent_run_status_supabase")
    @patch("app.integrations.agent_runs.create_supabase")
    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_update_falls_back_to_supabase(
        self,
        mock_dsn: MagicMock,
        mock_create_sb: MagicMock,
        mock_sb_update: MagicMock,
    ) -> None:
        mock_dsn.return_value = None
        mock_create_sb.return_value = MagicMock()
        run_id = UUID("22222222-2222-2222-2222-222222222222")
        mock_sb_update.return_value = {"id": str(run_id), "status": "denied"}
        settings = Settings(supabase_url="https://x.supabase.co", supabase_service_role_key="k")

        row = update_agent_run_status(
            settings,
            run_id,
            "denied",
            practice_id="practice-1",
        )

        self.assertEqual(row["status"], "denied")
        mock_sb_update.assert_called_once()

    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_update_rejects_invalid_resolve_status(self, mock_dsn: MagicMock) -> None:
        mock_dsn.return_value = None
        settings = Settings()

        with self.assertRaises(AgentRunTransitionError):
            update_agent_run_status(
                settings,
                UUID("33333333-3333-3333-3333-333333333333"),
                "pending_review",
                practice_id="practice-1",
            )


def _build_resolve_client(
    *,
    practice_id: str = "practice-1",
    role: str | None = "billing_lead",
) -> TestClient:
    app = FastAPI()
    app.include_router(rcm.router)

    async def _tenant_override() -> PracticeContext:
        return PracticeContext(
            practice_id=practice_id,
            role=role,  # type: ignore[arg-type]
            principal=MagicMock(),
        )

    app.dependency_overrides[require_practice_context] = _tenant_override
    app.dependency_overrides[get_settings] = lambda: Settings(neon_database_url="postgresql://neon")
    return TestClient(app)


class ResolvePriorAuthRunEndpointTests(unittest.TestCase):
    @patch("app.api.routes.rcm.update_agent_run_status")
    def test_resolve_success(self, mock_update: MagicMock) -> None:
        run_id = UUID("44444444-4444-4444-4444-444444444444")
        mock_update.return_value = {
            "id": str(run_id),
            "status": "approved",
            "agent": AGENT_PRIOR_AUTH,
        }
        client = _build_resolve_client(role="admin")

        resp = client.post(
            f"/agents/prior-auth/runs/{run_id}/resolve",
            json={"status": "approved", "reason": "Verified with payer"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["message"], "Agent run resolved successfully")
        mock_update.assert_called_once()

    @patch("app.api.routes.rcm.update_agent_run_status")
    def test_resolve_forbidden_for_read_only(self, mock_update: MagicMock) -> None:
        client = _build_resolve_client(role="read_only")
        run_id = UUID("55555555-5555-5555-5555-555555555555")

        resp = client.post(
            f"/agents/prior-auth/runs/{run_id}/resolve",
            json={"status": "approved"},
        )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "role_forbidden")
        mock_update.assert_not_called()

    @patch("app.api.routes.rcm.update_agent_run_status")
    def test_resolve_not_found(self, mock_update: MagicMock) -> None:
        mock_update.side_effect = AgentRunNotFoundError("missing")
        client = _build_resolve_client()
        run_id = UUID("66666666-6666-6666-6666-666666666666")

        resp = client.post(
            f"/agents/prior-auth/runs/{run_id}/resolve",
            json={"status": "expired"},
        )

        self.assertEqual(resp.status_code, 404)

    @patch("app.api.routes.rcm.update_agent_run_status")
    def test_resolve_invalid_transition(self, mock_update: MagicMock) -> None:
        mock_update.side_effect = AgentRunTransitionError("bad transition")
        client = _build_resolve_client()
        run_id = UUID("77777777-7777-7777-7777-777777777777")

        resp = client.post(
            f"/agents/prior-auth/runs/{run_id}/resolve",
            json={"status": "denied"},
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"], "invalid_transition")


if __name__ == "__main__":
    unittest.main()
