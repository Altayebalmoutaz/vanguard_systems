"""Unified OpenRouter HTTP client with retries and timeout."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def openrouter_chat_completion(
    *,
    api_key: str,
    payload: dict[str, Any],
    http_referer: str,
    app_name: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """
    POST to OpenRouter chat completions with bounded retries on transient failures.

    Retries HTTP 429 and 5xx responses plus connection/timeouts. Non-retryable
    4xx errors propagate immediately.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": http_referer,
        "X-Title": app_name,
    }
    attempts = max(1, max_retries)
    last_exc: Exception | None = None

    with httpx.Client(timeout=timeout_seconds) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = client.post(OPENROUTER_URL, headers=headers, json=payload)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    logger.warning(
                        "OpenRouter retryable HTTP %s (attempt %s/%s)",
                        response.status_code,
                        attempt,
                        attempts,
                    )
                    time.sleep(retry_backoff_seconds * attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                logger.warning(
                    "OpenRouter timeout (attempt %s/%s)",
                    attempt,
                    attempts,
                )
                time.sleep(retry_backoff_seconds * attempt)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_STATUS_CODES or attempt >= attempts:
                    raise
                last_exc = exc
                logger.warning(
                    "OpenRouter HTTP error %s (attempt %s/%s)",
                    exc.response.status_code,
                    attempt,
                    attempts,
                )
                time.sleep(retry_backoff_seconds * attempt)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                logger.warning(
                    "OpenRouter transport error (attempt %s/%s): %s",
                    attempt,
                    attempts,
                    type(exc).__name__,
                )
                time.sleep(retry_backoff_seconds * attempt)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter request failed without exception")
