"""Tests for durable dentist decision capture."""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.coding.decisions import run_record_decision
from app.coding.errors import CodingPersistenceError, CodingRunNotFoundError
from app.coding.main import app as coding_app
from app.coding.schemas import CodingDecisionRequest
from app.config import Settings

RUN_ID = UUID("55555555-5555-5555-5555-555555555555")


def _request() -> CodingDecisionRequest:
    return CodingDecisionRequest.model_validate(
        {
            "practice_id": "practice-a",
            "coding_run_id": str(RUN_ID),
            "decided_by": "dentist-a",
            "decisions": [{"line_id": "1", "action": "approved"}],
        }
    )


def _stored_run() -> dict[str, object]:
    return {
        "id": str(RUN_ID),
        "payer_id": "payer-a",
        "response_payload": {
            "recommendations": [{"line_id": "1", "cdt_code": "D0120"}],
        },
    }


def test_failed_decision_write_raises_without_emitting_metrics() -> None:
    with (
        patch("app.coding.decisions.fetch_run_by_id", return_value=_stored_run()),
        patch("app.coding.decisions.insert_coding_decisions", return_value=0),
        patch("app.coding.decisions.inc") as mock_inc,
    ):
        with pytest.raises(CodingPersistenceError, match="persisted 0 of 1"):
            run_record_decision(_request(), settings=Settings())

    mock_inc.assert_not_called()


def test_unknown_coding_run_is_rejected_before_insert() -> None:
    with (
        patch("app.coding.decisions.fetch_run_by_id", return_value=None),
        patch("app.coding.decisions.insert_coding_decisions") as mock_insert,
    ):
        with pytest.raises(CodingRunNotFoundError):
            run_record_decision(_request(), settings=Settings())

    mock_insert.assert_not_called()


def test_successful_decision_write_emits_metrics() -> None:
    with (
        patch("app.coding.decisions.fetch_run_by_id", return_value=_stored_run()),
        patch("app.coding.decisions.insert_coding_decisions", return_value=1),
        patch("app.coding.decisions.write_audit_log"),
        patch("app.coding.decisions.inc") as mock_inc,
    ):
        response = run_record_decision(_request(), settings=Settings())

    assert response.recorded == 1
    assert response.status == "recorded"
    assert mock_inc.call_count == 2


def test_decision_endpoint_returns_503_for_persistence_failure() -> None:
    body = _request().model_dump(mode="json")
    with patch(
        "app.coding.main.run_record_decision",
        side_effect=CodingPersistenceError("database unavailable"),
    ):
        response = TestClient(coding_app).post("/v1/decision", json=body)

    assert response.status_code == 503
    assert (
        response.json()["detail"]["message"]
        == "Coding decision could not be saved; retry the request"
    )


def test_decision_endpoint_returns_404_for_unknown_run() -> None:
    body = _request().model_dump(mode="json")
    with patch(
        "app.coding.main.run_record_decision",
        side_effect=CodingRunNotFoundError("missing run"),
    ):
        response = TestClient(coding_app).post("/v1/decision", json=body)

    assert response.status_code == 404
    assert response.json()["detail"]["message"] == "Coding run not found for this practice"
