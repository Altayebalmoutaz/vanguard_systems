"""Solo Coding Agent sub-app (scribe-facing v1 API)."""

from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from app.coding.main import app as coding_app

        return coding_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
