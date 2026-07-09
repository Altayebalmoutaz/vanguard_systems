from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from app.db.json_safe import json_safe
from app.eligibility.db_phi import _json_safe


def test_json_safe_converts_datetime_date_and_uuid() -> None:
    payload = {
        "when": datetime(2026, 7, 6, 12, 0, 0),
        "until": date(2026, 12, 31),
        "check_id": UUID("39390b1a-684f-4ba7-a4bf-adcc95fa305b"),
        "nested": [{"waiting_period_end": date(2027, 1, 1)}],
    }
    safe = json_safe(payload)
    assert safe == _json_safe(payload)
    assert safe["when"] == "2026-07-06T12:00:00"
    assert safe["until"] == "2026-12-31"
    assert safe["check_id"] == "39390b1a-684f-4ba7-a4bf-adcc95fa305b"
    assert safe["nested"][0]["waiting_period_end"] == "2027-01-01"

    import json

    json.dumps(safe)


def test_pipeline_create_payload_with_datetime_is_jsonb_safe() -> None:
    """Regression: OD writeback enqueue embeds primary_result with date/datetime."""
    import json

    payload = {
        "pat_num": 35,
        "primary_result": {
            "checked_at": datetime(2026, 7, 9, 3, 38, 0),
            "plan_end": date(2026, 12, 31),
            "check_id": UUID("39390b1a-684f-4ba7-a4bf-adcc95fa305b"),
        },
    }
    # Must not raise TypeError: Object of type datetime is not JSON serializable
    json.dumps(json_safe(payload))
