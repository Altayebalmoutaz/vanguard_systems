"""JSON coercion helpers for psycopg ``Jsonb`` payloads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID


def json_safe(value: Any) -> Any:
    """Recursively coerce values so ``Jsonb(...)`` never sees datetime/date/UUID."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)
