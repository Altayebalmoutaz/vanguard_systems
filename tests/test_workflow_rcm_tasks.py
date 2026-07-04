"""Tests for Workflow OS rcm_tasks helpers."""

from __future__ import annotations

import unittest

from app.workflow.rcm_tasks import (
    should_route_coding_to_hitl,
    should_route_denial_to_hitl,
    should_route_prior_auth_to_hitl,
    should_route_to_hitl,
)


class WorkflowGatingTests(unittest.TestCase):
    def test_coding_confidence_gate(self) -> None:
        self.assertTrue(should_route_to_hitl(0.5, 0.85))
        self.assertFalse(should_route_to_hitl(0.9, 0.85))

    def test_coding_payer_flags_gate(self) -> None:
        self.assertTrue(
            should_route_coding_to_hitl(0.95, 0.85, payer_flags=["needs_human_review"])
        )

    def test_prior_auth_gate(self) -> None:
        self.assertTrue(should_route_prior_auth_to_hitl({"requires_auth": True}))
        self.assertTrue(
            should_route_prior_auth_to_hitl({"required_documents": ["panoramic_xray"]})
        )
        self.assertTrue(should_route_prior_auth_to_hitl({"risk_level": "high"}))
        self.assertFalse(should_route_prior_auth_to_hitl({"risk_level": "low"}))

    def test_denial_gate(self) -> None:
        self.assertTrue(should_route_denial_to_hitl({"requires_human_review": True}))
        self.assertTrue(
            should_route_denial_to_hitl(
                {"status": "denied", "next_action": "appeal", "requires_human_review": False}
            )
        )
        self.assertFalse(should_route_denial_to_hitl({"status": "paid", "next_action": "none"}))


if __name__ == "__main__":
    unittest.main()
