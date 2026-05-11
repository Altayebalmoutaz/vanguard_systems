"""Sanitized HTTPException helpers — never leak raw exception text to clients.

Historical pattern across `app/api/routes/*.py` and `app/eligibility/main.py` was:

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

The string form of `e` frequently contained PostgREST error rows, Supabase row
payloads, OpenRouter response bodies, and Stedi 271 fragments — all of which
can include PHI (name, DOB, member ID). This module provides a single helper
that:

* Logs the full exception under a stable `error_id` (with PHI scrubbed by
  :func:`app.security.phi.scrub_for_log`).
* Returns an :class:`fastapi.HTTPException` whose `detail` carries only a short
  status-appropriate message and the same `error_id` so operators can
  correlate user complaints to log lines.

Routes that already construct their own structured `detail` (e.g. layer-1
validation errors with explicit field codes) can keep doing so — those are
authored strings, not raw exception text.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from fastapi import HTTPException

from app.security.phi import scrub_for_log

_LOGGER = logging.getLogger("vanguard.api.errors")

_DEFAULT_PUBLIC_MESSAGES: dict[int, str] = {
    400: "Bad request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Unprocessable entity",
    500: "Internal server error",
    502: "Upstream provider error",
    503: "Service temporarily unavailable",
    504: "Upstream provider timed out",
}


def sanitized_http_exception(
    status_code: int,
    *,
    public_message: str | None = None,
    log_message: str = "",
    exc: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> HTTPException:
    """Build a sanitized HTTPException.

    Parameters
    ----------
    status_code:
        Status to return to the client.
    public_message:
        Optional short message returned in `detail.message`. Must NOT contain
        any data derived from the exception or request payload — only
        operator-authored strings (e.g. ``"Failed to run coding agent"``).
    log_message:
        Free-text message logged alongside the exception. Scrubbed before
        emission, so it's safe to include exception context here.
    exc:
        The original exception. Logged with traceback (also scrubbed). Never
        propagated to the client.
    extra:
        Additional structured fields to embed in `detail` (e.g. ``{"code":
        "INVALID_PRIMARY_PAYER"}``). Use only for caller-authored values.
    """
    error_id = uuid.uuid4().hex
    safe_msg = public_message or _DEFAULT_PUBLIC_MESSAGES.get(status_code, "Request failed")
    logged_text = log_message or safe_msg

    if exc is not None:
        # IMPORTANT: do NOT pass `exc_info=exc` here. Python's logging handlers
        # call `traceback.format_exception(...)` on `exc_info`, which renders
        # the original (unscrubbed) exception message as part of the traceback.
        # We instead serialise + scrub the traceback ourselves and pass it as a
        # plain string field, so PHI in `str(exc)` is never written to any log
        # sink in clear text.
        tb_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        _LOGGER.error(
            "%s [error_id=%s status=%s] exc=%s\n%s",
            scrub_for_log(logged_text),
            error_id,
            status_code,
            scrub_for_log(repr(exc)),
            scrub_for_log(tb_text),
        )
    else:
        _LOGGER.error(
            "%s [error_id=%s status=%s]",
            scrub_for_log(logged_text),
            error_id,
            status_code,
        )

    detail: dict[str, Any] = {"error_id": error_id, "message": safe_msg}
    if extra:
        detail.update({k: v for k, v in extra.items() if k not in {"error_id", "message"}})
    return HTTPException(status_code=status_code, detail=detail)


__all__ = ["sanitized_http_exception"]
