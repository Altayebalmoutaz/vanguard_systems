"""Tests for pipeline queue, gating, and async job routes."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import Principal, require_principal
from app.api.tenancy import require_practice_context
from app.config import Settings, get_settings
from app.pipeline.gating import extract_coding_confidence, should_route_to_hitl


class ConfidenceGatingTests(unittest.TestCase):
    def test_extract_confidence(self) -> None:
        self.assertEqual(extract_coding_confidence({"coding": {"confidence": 0.9}}), 0.9)

    def test_route_to_hitl_below_threshold(self) -> None:
        self.assertTrue(should_route_to_hitl(0.5, 0.85))
        self.assertFalse(should_route_to_hitl(0.9, 0.85))
        self.assertTrue(should_route_to_hitl(None, 0.85))


class PipelineJobRouteTests(unittest.TestCase):
    def _client(self, settings: Settings) -> TestClient:
        from app.api.routes import rcm

        app = FastAPI()
        app.include_router(rcm.router)

        async def _principal() -> Principal:
            return Principal(
                kind="jwt",
                subject="user-1",
                claims={},
                practice_roles=(),
            )

        async def _tenant() -> object:
            from app.api.tenancy import PracticeContext

            return PracticeContext(
                practice_id="practice-1", role="admin", principal=await _principal()
            )

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[require_principal] = _principal
        app.dependency_overrides[require_practice_context] = _tenant
        return TestClient(app)

    @patch("app.api.routes.rcm.create_pipeline_run")
    def test_enqueue_returns_run_id(self, mock_create: MagicMock) -> None:
        mock_create.return_value = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        settings = Settings(neon_database_url="postgresql://neon", require_auth=False)
        client = self._client(settings)

        resp = client.post(
            "/agents/rcm/full-pipeline/jobs",
            json={
                "clinical_note": "Crown prep",
                "patient_age": 40,
                "insurance": "Delta",
                "encounter_id": "enc-123",
            },
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["run_id"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    @patch("app.api.routes.rcm.get_pipeline_run")
    def test_poll_completed_job(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "status": "completed",
            "run_type": "full_rcm_pipeline",
            "result": {"coding": {"confidence": 0.9}},
            "error_message": None,
            "error_code": None,
        }
        settings = Settings(neon_database_url="postgresql://neon", require_auth=False)
        client = self._client(settings)

        resp = client.get("/agents/rcm/full-pipeline/jobs/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "completed")
        self.assertIn("coding", resp.json()["result"])


if __name__ == "__main__":
    unittest.main()
