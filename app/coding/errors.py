"""Coding-agent domain errors."""


class CodingPersistenceError(RuntimeError):
    """A coding run or dentist decision could not be persisted."""


class CodingRunNotFoundError(LookupError):
    """A decision referenced no coding run in the requested practice."""
