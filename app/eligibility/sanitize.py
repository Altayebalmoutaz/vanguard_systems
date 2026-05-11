"""
Backward-compatible shim.

PHI scrubbing now lives in :mod:`app.security.phi`. This module re-exports the original public
surface so existing eligibility-side imports keep working.
"""

from app.security.phi import scrub_detail_for_storage, scrub_for_log

__all__ = ["scrub_detail_for_storage", "scrub_for_log"]
