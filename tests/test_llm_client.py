"""Tests for unified OpenRouter LLM client retries and timeout."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.llm.client import OPENROUTER_URL, openrouter_chat_completion


@respx.mock
def test_openrouter_success_first_attempt() -> None:
    route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )
    )
    result = openrouter_chat_completion(
        api_key="test-key",
        payload={"model": "openai/gpt-4o-mini", "messages": []},
        http_referer="https://localhost",
        app_name="test-app",
        max_retries=1,
    )
    assert result["choices"][0]["message"]["content"] == '{"ok": true}'
    assert route.call_count == 1


@respx.mock
def test_openrouter_retries_on_429() -> None:
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            ),
        ]
    )
    result = openrouter_chat_completion(
        api_key="test-key",
        payload={"model": "openai/gpt-4o-mini", "messages": []},
        http_referer="https://localhost",
        app_name="test-app",
        max_retries=2,
        retry_backoff_seconds=0.0,
    )
    assert route.call_count == 2
    assert result["choices"][0]["message"]["content"] == '{"ok": true}'


@respx.mock
def test_openrouter_raises_after_max_retries() -> None:
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(503, json={"error": "unavailable"}))
    with pytest.raises(httpx.HTTPStatusError):
        openrouter_chat_completion(
            api_key="test-key",
            payload={"model": "openai/gpt-4o-mini", "messages": []},
            http_referer="https://localhost",
            app_name="test-app",
            max_retries=2,
            retry_backoff_seconds=0.0,
        )


@respx.mock
def test_openrouter_non_retryable_4xx_raises_immediately() -> None:
    route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(401, json={"error": "auth"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        openrouter_chat_completion(
            api_key="bad-key",
            payload={"model": "openai/gpt-4o-mini", "messages": []},
            http_referer="https://localhost",
            app_name="test-app",
            max_retries=3,
        )
    assert route.call_count == 1
