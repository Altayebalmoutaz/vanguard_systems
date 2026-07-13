"""
Safe Harbor de-identification ETL (Neon PHI plane → non-PHI eval plane).

This is the **only** sanctioned bridge between PHI and non-PHI data stores. It is
**not** Presidio scrubbing (see ``app.security.phi``) — scrubbing is risk mitigation for
LLM/log egress; de-identification here follows HIPAA Safe Harbor removal/generalization:

* Strip the 18 identifier categories (field-name denylist + pattern checks).
* Remove linkage keys (``patient_id``, ``encounter_id``, …) so rows cannot be rejoined.
* Generalize dates to **year only** (no month/day).
* **Fail-closed**: any validation error raises :class:`DeidentificationError` and the
  record must not be written to the non-PHI plane.

Phase 5 wires :class:`DeidentificationETL.publish` to the eval harness / Supabase eval
tables. Until then, use :func:`deidentify_record` in tests and offline tooling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Field names that must never appear on the non-PHI plane (Safe Harbor + app PHI).
SAFE_HARBOR_IDENTIFIER_KEYS: frozenset[str] = frozenset(
    {
        # Names
        "name",
        "patient_name",
        "first_name",
        "last_name",
        "full_name",
        "provider_name",
        "subscriber_name",
        "member_name",
        # Geographic (smaller than state)
        "address",
        "street",
        "street_address",
        "city",
        "postal_code",
        "zip",
        "zip_code",
        "county",
        # Contact
        "phone",
        "phone_number",
        "fax",
        "fax_number",
        "email",
        "email_address",
        # Government / plan identifiers
        "ssn",
        "social_security_number",
        "mbi",
        "subscriber_id",
        "member_id",
        "insurance_id",
        "medical_record_number",
        "mrn",
        "account_number",
        "certificate_number",
        "license_number",
        # Device / vehicle / web
        "vin",
        "license_plate",
        "device_id",
        "device_serial",
        "url",
        "ip_address",
        # Clinical payloads
        "raw_response",
        "clinical_note",
        "raw_note",
        "note_summary",
        "demographics_block",
        "input_snapshot",
        "input_json",
        "output_json",
        "pipeline_json",
        "source_pipeline_json",
        "patient",
        "subscriber",
        "appointment_time",
    }
)

# Keys that re-identify rows if retained — always removed (not generalized).
LINKAGE_KEYS: frozenset[str] = frozenset(
    {
        "patient_id",
        "encounter_id",
        "request_id",
        "check_id",
        "primary_check_id",
        "secondary_check_id",
        "parent_request_id",
        "idempotency_key",
        "backend_record_id",
        "backend_claim_id",
        "task_id",
        "decision_id",
        "claim_reference",
        "insurance_id",
        "created_by",
    }
)

# Allowed suffix for generalized date fields emitted by this module.
_YEAR_FIELD_SUFFIX = "_year"

DATE_FIELDS_TO_GENERALIZE: frozenset[str] = frozenset(
    {
        "dob",
        "date_of_birth",
        "patient_dob",
        "birth_date",
        "appointment_date",
        "checked_at",
        "visit_date",
        "service_date",
        "analyzed_at",
        "accepted_at",
        "created_at",
        "completed_at",
    }
)

_EMAIL_PATTERN = re.compile(r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_PATTERN = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")


class DeidentificationError(Exception):
    """Raised when a record cannot be safely de-identified (fail-closed)."""


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def _extract_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 1900 <= value <= 2100:
            return value
        raise DeidentificationError(f"integer date year out of range: {value}")
    if isinstance(value, date):
        return value.year
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d{4}", text):
            return int(text)
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(text[:10], fmt).year
            except ValueError:
                continue
        raise DeidentificationError(f"cannot generalize date value: {value!r}")
    raise DeidentificationError(f"unsupported date type: {type(value).__name__}")


def _scan_string_for_residual_phi(text: str, *, field_name: str) -> None:
    if _SSN_PATTERN.search(text):
        raise DeidentificationError(f"residual SSN-like pattern in {field_name}")
    if _EMAIL_PATTERN.search(text):
        raise DeidentificationError(f"residual email in {field_name}")
    if _PHONE_PATTERN.search(text):
        raise DeidentificationError(f"residual phone in {field_name}")


def _is_year_only_field(key: str) -> bool:
    return _normalize_key(key).endswith(_YEAR_FIELD_SUFFIX)


def deidentify_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    Transform one PHI-shaped dict into a Safe Harbor de-identified dict.

    Raises :class:`DeidentificationError` if the result still contains forbidden
    identifiers or linkage keys.
    """
    if not isinstance(record, dict):
        raise DeidentificationError("source record must be a dict")

    out: dict[str, Any] = {}

    for key, value in record.items():
        norm = _normalize_key(key)

        if norm in LINKAGE_KEYS:
            continue

        if norm in DATE_FIELDS_TO_GENERALIZE:
            year = _extract_year(value)
            if year is not None:
                base = norm.removesuffix("_at").removesuffix("_date")
                if base == "date_of_birth":
                    base = "birth"
                out[f"{base}_year"] = year
            continue

        if norm in SAFE_HARBOR_IDENTIFIER_KEYS:
            continue

        if _is_year_only_field(key):
            out[key] = value
            continue

        if isinstance(value, dict):
            out[key] = deidentify_record(value)
        elif isinstance(value, list):
            out[key] = [
                deidentify_record(item) if isinstance(item, dict) else item for item in value
            ]
        elif isinstance(value, str):
            _scan_string_for_residual_phi(value, field_name=key)
            out[key] = value
        else:
            out[key] = value

    validate_deidentified(out)
    return out


def validate_deidentified(record: dict[str, Any], *, path: str = "") -> None:
    """Fail-closed validation before writing to the non-PHI plane."""
    if not isinstance(record, dict):
        raise DeidentificationError(f"{path or 'record'} must be a dict")

    for key, value in record.items():
        norm = _normalize_key(key)
        label = f"{path}.{key}" if path else key

        if norm in LINKAGE_KEYS or norm in SAFE_HARBOR_IDENTIFIER_KEYS:
            raise DeidentificationError(f"forbidden key remains: {label}")

        if isinstance(value, dict):
            validate_deidentified(value, path=label)
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    validate_deidentified(item, path=f"{label}[{idx}]")
                elif isinstance(item, str):
                    _scan_string_for_residual_phi(item, field_name=f"{label}[{idx}]")
        elif isinstance(value, str):
            _scan_string_for_residual_phi(value, field_name=label)


@dataclass
class DeidentificationETL:
    """
    Batch-oriented ETL skeleton: read PHI-shaped rows, emit eval-plane rows.

    ``publish`` is intentionally unimplemented until Phase 5 eval storage exists.
    """

    dataset_name: str
    schema_version: str = "safe_harbor_v1"
    stats: dict[str, int] = field(default_factory=dict)

    def transform(self, source: dict[str, Any]) -> dict[str, Any]:
        """De-identify one source row and update counters."""
        result = deidentify_record(source)
        self.stats["transformed"] = self.stats.get("transformed", 0) + 1
        result["_deid"] = {
            "dataset": self.dataset_name,
            "schema_version": self.schema_version,
        }
        return result

    def transform_batch(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.transform(row) for row in sources]

    def publish(self, record: dict[str, Any], *, sink: str = "evals") -> None:
        """Write to the non-PHI eval plane (Phase 5)."""
        validate_deidentified(record)
        raise NotImplementedError(
            f"eval-plane publish to {sink!r} is not wired until Phase 5 "
            "(evals harness + de-identified corpus tables on Supabase)."
        )
