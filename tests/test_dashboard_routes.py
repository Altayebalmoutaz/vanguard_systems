"""Tests for dashboard BFF routes (mocked Neon store)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import dashboard
from app.api.tenancy import PracticeContext, require_practice_context
from app.config import Settings, get_settings
from app.dashboard.store import (
    DashboardPatientNotFoundError,
    DashboardRequestNotFoundError,
    compute_status_label,
)
from app.db.connection import NeonNotConfiguredError


def _build_client(*, neon_configured: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard.router)

    async def _tenant_override() -> PracticeContext:
        return PracticeContext(
            practice_id="practice-1",
            role="admin",
            principal=MagicMock(),
        )

    app.dependency_overrides[require_practice_context] = _tenant_override
    if neon_configured:
        app.dependency_overrides[get_settings] = lambda: Settings(
            neon_database_url="postgresql://neon"
        )
    else:
        app.dependency_overrides[get_settings] = lambda: Settings()
    return TestClient(app)


class DashboardStatusLabelTests(unittest.TestCase):
    def test_verified_when_check_complete(self) -> None:
        label = compute_status_label(
            request_status="completed",
            is_active=True,
            response_complete=True,
            missing_fields=[],
            integrity_warnings=[],
            routing_status="CLEARED",
            has_check=True,
        )
        self.assertEqual(label, "Verified")

    def test_needs_attention_when_missing_fields(self) -> None:
        label = compute_status_label(
            request_status="completed",
            is_active=True,
            response_complete=True,
            missing_fields=["deductible_remaining"],
            integrity_warnings=[],
            routing_status="CLEARED",
            has_check=True,
        )
        self.assertEqual(label, "Needs Attention")

    def test_verified_when_only_informational_integrity_warnings(self) -> None:
        label = compute_status_label(
            request_status="completed",
            is_active=True,
            response_complete=True,
            missing_fields=[],
            integrity_warnings=[
                "layer3_clamp:deductible",
                "important_field_null:annual_max",
            ],
            routing_status="CLEARED",
            has_check=True,
        )
        self.assertEqual(label, "Verified")

    def test_needs_attention_when_blocking_integrity_warning(self) -> None:
        label = compute_status_label(
            request_status="completed",
            is_active=True,
            response_complete=True,
            missing_fields=[],
            integrity_warnings=["layer3_clamp:deductible", "stale_benefit_year"],
            routing_status="CLEARED",
            has_check=True,
        )
        self.assertEqual(label, "Needs Attention")


class DashboardRouteTests(unittest.TestCase):
    @patch("app.api.routes.dashboard.list_eligibility_queue")
    def test_eligibility_queue_returns_rows(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [{"request_id": "req-1", "status_label": "Queued"}]
        client = _build_client()

        resp = client.get("/dashboard/eligibility/queue")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["practice_id"], "practice-1")
        self.assertEqual(body["rows"][0]["request_id"], "req-1")
        mock_list.assert_called_once()

    @patch("app.api.routes.dashboard.list_eligibility_queue")
    def test_eligibility_queue_neon_missing_returns_503(self, mock_list: MagicMock) -> None:
        mock_list.side_effect = NeonNotConfiguredError("NEON_DATABASE_URL is not configured")
        client = _build_client(neon_configured=False)

        resp = client.get("/dashboard/eligibility/queue")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["message"], "Database is unavailable")

    @patch("app.api.routes.dashboard.get_eligibility_agent_settings_row")
    def test_eligibility_settings(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value = {"practice_id": "practice-1", "auto_check_enabled": True}
        client = _build_client()

        resp = client.get("/dashboard/eligibility/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["settings"]["auto_check_enabled"])

    @patch("app.api.routes.dashboard.list_procedure_estimates_for_request")
    def test_request_estimates(self, mock_estimates: MagicMock) -> None:
        request_id = UUID("11111111-1111-1111-1111-111111111111")
        mock_estimates.return_value = [{"id": "est-1", "cdt_code": "D0120"}]
        client = _build_client()

        resp = client.get(f"/dashboard/eligibility/requests/{request_id}/estimates")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["estimates"][0]["cdt_code"], "D0120")

    @patch("app.api.routes.dashboard.list_procedure_estimates_for_request")
    def test_request_estimates_not_found(self, mock_estimates: MagicMock) -> None:
        request_id = UUID("11111111-1111-1111-1111-111111111111")
        mock_estimates.side_effect = DashboardRequestNotFoundError("missing")
        client = _build_client()

        resp = client.get(f"/dashboard/eligibility/requests/{request_id}/estimates")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "eligibility_request_not_found")

    @patch("app.api.routes.dashboard.list_eligibility_request_events")
    def test_request_events(self, mock_events: MagicMock) -> None:
        request_id = UUID("22222222-2222-2222-2222-222222222222")
        mock_events.return_value = [{"id": "evt-1", "event_type": "queued"}]
        client = _build_client()

        resp = client.get(f"/dashboard/eligibility/requests/{request_id}/events")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["events"][0]["event_type"], "queued")

    @patch("app.api.routes.dashboard.list_eligibility_activity")
    def test_eligibility_activity(self, mock_activity: MagicMock) -> None:
        mock_activity.return_value = [{"id": "evt-2", "event_type": "completed"}]
        client = _build_client()

        resp = client.get("/dashboard/eligibility/activity?limit=25")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["events"]), 1)
        mock_activity.assert_called_once()

    @patch("app.api.routes.dashboard.create_eligibility_request")
    def test_create_eligibility_request(self, mock_create: MagicMock) -> None:
        mock_create.return_value = {
            "id": "33333333-3333-3333-3333-333333333333",
            "patient_id": "44444444-4444-4444-4444-444444444444",
            "status": "queued",
            "created_at": "2026-07-04T10:00:00+00:00",
        }
        client = _build_client()

        resp = client.post(
            "/dashboard/eligibility/requests",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "dob": "1990-01-15",
                "subscriber_id": "SUB123",
                "primary_payer_id": "84103",
                "cdt_codes": ["D0120"],
            },
        )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["request"]["status"], "queued")
        mock_create.assert_called_once()

    @patch("app.api.routes.dashboard.list_hitl_tasks")
    def test_hitl_tasks_pending(self, mock_tasks: MagicMock) -> None:
        mock_tasks.return_value = [{"id": "task-1", "status": "pending"}]
        client = _build_client()

        resp = client.get("/dashboard/hitl/tasks?status=pending")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tasks"][0]["status"], "pending")
        mock_tasks.assert_called_once()

    @patch("app.api.routes.dashboard.resolve_hitl_task")
    def test_hitl_task_resolve(self, mock_resolve: MagicMock) -> None:
        task_id = UUID("77777777-7777-7777-7777-777777777777")
        mock_resolve.return_value = {
            "task_id": str(task_id),
            "status": "approved",
            "accepted_claim_id": "acc-1",
            "task": {"id": str(task_id), "status": "approved"},
        }
        client = _build_client()

        resp = client.post(
            f"/dashboard/hitl/tasks/{task_id}/resolve",
            json={"action": "approve", "final_codes": ["D0120"]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["accepted_claim_id"], "acc-1")
        mock_resolve.assert_called_once()

    @patch("app.api.routes.dashboard.list_claim_cases")
    def test_claim_cases_route(self, mock_claims: MagicMock) -> None:
        mock_claims.return_value = [{"claim_id": "claim-1", "status": "draft"}]
        client = _build_client()

        resp = client.get("/dashboard/claims/cases")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["cases"][0]["claim_id"], "claim-1")

    @patch("app.api.routes.dashboard.get_dashboard_overview")
    def test_overview_route(self, mock_overview: MagicMock) -> None:
        mock_overview.return_value = {"worklist": [], "kpis": {"clean_claim_rate": 90.0}}
        client = _build_client()

        resp = client.get("/dashboard/overview")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kpis"]["clean_claim_rate"], 90.0)

    @patch("app.api.routes.dashboard.get_patient_360")
    def test_patient_360(self, mock_profile: MagicMock) -> None:
        patient_id = UUID("55555555-5555-5555-5555-555555555555")
        mock_profile.return_value = {
            "patient": {"id": str(patient_id), "name": "Jane Doe"},
            "latest_eligibility_check": {"id": "check-1"},
            "agent_runs": [{"id": "run-1"}],
        }
        client = _build_client()

        resp = client.get(f"/dashboard/patients/{patient_id}")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["patient"]["name"], "Jane Doe")
        self.assertEqual(body["latest_eligibility_check"]["id"], "check-1")
        self.assertEqual(body["agent_runs"][0]["id"], "run-1")

    @patch("app.api.routes.dashboard.get_settings")
    @patch("app.api.routes.dashboard.write_audit_log")
    @patch("app.api.routes.dashboard.run_copilot_chat")
    def test_copilot_chat_ok(
        self, mock_chat: MagicMock, mock_audit: MagicMock, mock_settings: MagicMock
    ) -> None:
        patient_id = UUID("55555555-5555-5555-5555-555555555555")
        mock_settings.return_value = Settings(
            neon_database_url="postgresql://neon",
            copilot_enabled=True,
            openrouter_api_key="test-key",
        )
        mock_chat.return_value = MagicMock(
            reply="Coverage is active.",
            tool_trace=[{"name": "get_patient_overview", "args": {}}],
            model="openai/gpt-4o-mini",
        )
        app_client = _build_client()

        resp = app_client.post(
            "/dashboard/copilot/chat",
            json={"patient_id": str(patient_id), "messages": [{"role": "user", "content": "Hi"}]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["reply"], "Coverage is active.")
        self.assertEqual(body["tool_trace"][0]["name"], "get_patient_overview")
        mock_audit.assert_called_once()

    @patch("app.api.routes.dashboard.get_settings")
    def test_copilot_chat_disabled(self, mock_settings: MagicMock) -> None:
        patient_id = UUID("55555555-5555-5555-5555-555555555555")
        mock_settings.return_value = Settings(
            neon_database_url="postgresql://neon",
            copilot_enabled=False,
        )
        client = _build_client()
        resp = client.post(
            "/dashboard/copilot/chat",
            json={"patient_id": str(patient_id), "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "copilot_disabled")

    def test_copilot_chat_role_forbidden(self) -> None:
        patient_id = UUID("55555555-5555-5555-5555-555555555555")
        app = FastAPI()
        app.include_router(dashboard.router)

        async def _tenant_override() -> PracticeContext:
            return PracticeContext(
                practice_id="practice-1",
                role="front_office",
                principal=MagicMock(subject="staff"),
            )

        app.dependency_overrides[require_practice_context] = _tenant_override
        app.dependency_overrides[get_settings] = lambda: Settings(
            neon_database_url="postgresql://neon",
            copilot_enabled=True,
            openrouter_api_key="test-key",
        )
        client = TestClient(app)
        resp = client.post(
            "/dashboard/copilot/chat",
            json={"patient_id": str(patient_id), "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "role_forbidden")

    @patch("app.api.routes.dashboard.get_settings")
    @patch("app.api.routes.dashboard.run_copilot_chat")
    def test_copilot_chat_patient_not_found(
        self, mock_chat: MagicMock, mock_settings: MagicMock
    ) -> None:
        patient_id = UUID("66666666-6666-6666-6666-666666666666")
        mock_settings.return_value = Settings(
            neon_database_url="postgresql://neon",
            copilot_enabled=True,
            openrouter_api_key="test-key",
        )
        mock_chat.side_effect = DashboardPatientNotFoundError("missing")
        app_client = _build_client()
        resp = app_client.post(
            "/dashboard/copilot/chat",
            json={"patient_id": str(patient_id), "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "patient_not_found")

    @patch("app.api.routes.dashboard.get_patient_360")
    def test_patient_360_not_found(self, mock_profile: MagicMock) -> None:
        patient_id = UUID("66666666-6666-6666-6666-666666666666")
        mock_profile.side_effect = DashboardPatientNotFoundError("missing")
        client = _build_client()

        resp = client.get(f"/dashboard/patients/{patient_id}")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "patient_not_found")


class EligibilityQueuePayloadTests(unittest.TestCase):
    def test_queue_sql_exposes_opendental_fields(self) -> None:
        from app.dashboard.store import _QUEUE_SQL

        self.assertIn("er.appointment_date", _QUEUE_SQL)
        self.assertIn("input_json->>'pat_num' as od_pat_num", _QUEUE_SQL)
        self.assertIn("input_json->>'source' as request_source", _QUEUE_SQL)
        self.assertIn("output_json->'opendental_writeback' as opendental_writeback", _QUEUE_SQL)

    def test_queue_sql_prefers_carrier_name_for_payer_label(self) -> None:
        from app.dashboard.store import _QUEUE_SQL

        self.assertIn("input_json->>'primary_carrier_name'", _QUEUE_SQL)
        self.assertIn("as payer_label", _QUEUE_SQL)

    def test_shape_dashboard_row_keeps_opendental_fields(self) -> None:
        from app.dashboard.store import _shape_dashboard_row

        shaped = _shape_dashboard_row(
            {
                "request_id": "req-1",
                "request_status": "completed",
                "priority": "medium",
                "appointment_date": "2026-08-13",
                "od_pat_num": "24",
                "request_source": "opendental",
                "opendental_writeback": {"status": "partial", "partial_failure": True},
                "check_id": "check-1",
                "is_active": True,
                "response_complete": True,
                "missing_fields": None,
                "integrity_warnings": None,
                "routing_status": "CLEARED",
            }
        )
        self.assertEqual(shaped["appointment_date"], "2026-08-13")
        self.assertEqual(shaped["od_pat_num"], "24")
        self.assertEqual(shaped["request_source"], "opendental")
        self.assertEqual(shaped["opendental_writeback"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
