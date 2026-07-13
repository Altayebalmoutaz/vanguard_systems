"""Tests for agent_runs Neon/Supabase routing."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.config import Settings
from app.integrations.agent_runs import (
    AGENT_PRIOR_AUTH,
    insert_agent_run,
    list_agent_runs_for_patient,
)


class AgentRunsRoutingTests(unittest.TestCase):
    @patch("app.integrations.agent_runs._insert_agent_run_neon")
    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_insert_prefers_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_neon_insert: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon_insert.return_value = UUID("11111111-1111-1111-1111-111111111111")
        settings = Settings(neon_database_url="postgresql://neon")

        run_id = insert_agent_run(
            settings,
            agent=AGENT_PRIOR_AUTH,
            input_json={"a": 1},
            output_json={"b": 2},
            practice_id="practice-1",
        )

        self.assertEqual(run_id, UUID("11111111-1111-1111-1111-111111111111"))
        mock_neon_insert.assert_called_once()

    @patch("app.integrations.agent_runs._insert_agent_run_supabase")
    @patch("app.integrations.agent_runs.create_supabase")
    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_insert_falls_back_to_supabase(
        self,
        mock_dsn: MagicMock,
        mock_create_sb: MagicMock,
        mock_sb_insert: MagicMock,
    ) -> None:
        mock_dsn.return_value = None
        mock_create_sb.return_value = MagicMock()
        mock_sb_insert.return_value = UUID("22222222-2222-2222-2222-222222222222")
        settings = Settings(supabase_url="https://x.supabase.co", supabase_service_role_key="k")

        run_id = insert_agent_run(
            settings,
            agent=AGENT_PRIOR_AUTH,
            input_json={},
            output_json={},
            practice_id="practice-1",
        )

        self.assertEqual(run_id, UUID("22222222-2222-2222-2222-222222222222"))
        mock_sb_insert.assert_called_once()

    def test_insert_requires_practice_id(self) -> None:
        settings = Settings()
        self.assertIsNone(
            insert_agent_run(
                settings,
                agent=AGENT_PRIOR_AUTH,
                input_json={},
                output_json={},
                practice_id=None,
            )
        )

    @patch("app.integrations.agent_runs._list_agent_runs_neon")
    @patch("app.integrations.agent_runs.get_neon_dsn")
    def test_list_prefers_neon_when_configured(
        self,
        mock_dsn: MagicMock,
        mock_neon_list: MagicMock,
    ) -> None:
        mock_dsn.return_value = "postgresql://neon"
        mock_neon_list.return_value = [{"id": "x"}]
        settings = Settings(neon_database_url="postgresql://neon")
        patient_id = UUID("33333333-3333-3333-3333-333333333333")

        rows = list_agent_runs_for_patient(
            settings,
            patient_id,
            practice_id="practice-1",
        )

        self.assertEqual(rows, [{"id": "x"}])
        mock_neon_list.assert_called_once()


if __name__ == "__main__":
    unittest.main()
