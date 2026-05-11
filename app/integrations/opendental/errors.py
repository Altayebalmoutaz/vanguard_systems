"""OpenDental integration error types."""

from __future__ import annotations


class OpenDentalError(Exception):
    """Base OpenDental integration error."""


class OpenDentalConfigError(OpenDentalError):
    """Raised when OpenDental settings are missing or invalid."""


class OpenDentalAPIError(OpenDentalError):
    """Raised when OpenDental API responds with an error."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenDentalMappingError(OpenDentalError):
    """Raised when OpenDental payload cannot be mapped to EligibilityRequest."""

