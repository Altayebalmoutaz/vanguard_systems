"""Tests for structured logging and correlation ID middleware."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logging_config import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    JsonFormatter,
    configure_logging,
    correlation_id_var,
)


def test_json_formatter_includes_correlation_id() -> None:
    token = correlation_id_var.set("corr-test-123")
    try:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["message"] == "hello"
        assert payload["correlation_id"] == "corr-test-123"
        assert payload["level"] == "INFO"
    finally:
        correlation_id_var.reset(token)


def test_configure_logging_sets_json_handler() -> None:
    configure_logging(log_level="WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


@pytest.fixture
def correlation_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, str | None]:
        return {"correlation_id": correlation_id_var.get()}

    return TestClient(app)


def test_correlation_middleware_generates_id(correlation_client: TestClient) -> None:
    response = correlation_client.get("/ping")
    assert response.status_code == 200
    cid = response.headers[CORRELATION_ID_HEADER]
    assert cid
    assert response.json()["correlation_id"] == cid


def test_correlation_middleware_propagates_incoming_id(correlation_client: TestClient) -> None:
    incoming = "client-supplied-id"
    response = correlation_client.get("/ping", headers={CORRELATION_ID_HEADER: incoming})
    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == incoming
    assert response.json()["correlation_id"] == incoming
