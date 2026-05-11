"""Layer 3+ — canonical eligibility record (contract for Layer 4 / Layer 6)."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

_NULLABLE_FIELD_NAMES = (
    "is_in_network",
    "is_covered",
    "procedure_covered",
    "deductible_remaining",
    "deductible_total",
    "max_remaining",
    "max_total",
    "copay",
    "coinsurance",
    "patient_responsibility",
)


class EligibilityCanonicalRecord(BaseModel):
    """
    Canonical snapshot after Layer 3 normalization.

    Optional scalar fields must not be silently None: every None must have a
    matching entry in ``null_reasons`` with a non-empty explanation.
    """

    is_active: bool
    is_in_network: bool | None = None
    is_covered: bool | None = None
    procedure_covered: bool | None = None
    deductible_remaining: float | None = None
    deductible_total: float | None = None
    max_remaining: float | None = None
    max_total: float | None = None
    copay: float | None = None
    coinsurance: float | None = None
    patient_responsibility: float | None = None
    coverage_confidence: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    response_complete: bool
    null_reasons: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_canonical_invariants(self) -> Self:
        if self.response_complete and self.missing_fields:
            raise ValueError("missing_fields must be empty when response_complete is True")

        if not self.is_active:
            if self.is_covered is not False:
                raise ValueError("when is_active is False, is_covered must be False, not None")
            if self.procedure_covered is not False:
                raise ValueError("when is_active is False, procedure_covered must be False, not None")

        expected_keys: set[str] = set()
        for name in _NULLABLE_FIELD_NAMES:
            if getattr(self, name) is None:
                expected_keys.add(name)

        actual_keys = set(self.null_reasons.keys())
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
            parts: list[str] = []
            if missing:
                parts.append(f"missing null_reasons for null fields: {missing}")
            if extra:
                parts.append(f"unexpected null_reasons keys (non-null fields or unknown): {extra}")
            raise ValueError("; ".join(parts))

        for field_name, reason in self.null_reasons.items():
            if not (reason and reason.strip()):
                raise ValueError(f"null_reasons[{field_name!r}] must be a non-empty reason string")

        return self
