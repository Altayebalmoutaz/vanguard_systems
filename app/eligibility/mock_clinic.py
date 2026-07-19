"""Synthetic demo clinic identifiers aligned with migration ``040_mock_clinic_practices_and_seed``.

Use optional ``practice_id`` / ``rendering_provider_npi`` on ``EligibilityRequest``
with seeded ``provider_payer_network`` rows so Layer 5 uses directory-based fee path.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MOCK_PRACTICE_ID = "vgd_mock_brooklyn"
DEFAULT_MOCK_RENDERING_NPI = "1104023674"
DEFAULT_MOCK_LOCATION_KEY = "site_main"

# Seeded in migration 041 (vgd_mock_brooklyn + NPI 1104023674): 84103, AMTAS00425, 62308, 10134,
# 52133, 60054, 77777 (site_main), 64246 (OON for regression). Associate NPI 1982654321 + 62308.

# Stedi dental mock patients used for live voice-agent demos. Live 271s often clear fully;
# force a recoverable gap so Layer 6 stays INCOMPLETE and Bland can auto-queue.
_VOICE_DEMO_FORCE_INCOMPLETE_PATIENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("jaguar", "dent"),
        ("elephant", "dent"),
    }
)
_VOICE_DEMO_MISSING_FIELD = "deductible_remaining"
_VOICE_DEMO_WARNING = "voice_demo_force_incomplete"


def _norm_name(value: Any) -> str:
    return str(value or "").strip().lower()


def is_voice_demo_force_incomplete_patient(
    *,
    first_name: Any = None,
    last_name: Any = None,
    practice_id: Any = None,
) -> bool:
    """True for Jaguar/Elephant Dent on the seeded mock practice."""
    practice = str(practice_id or "").strip()
    if practice and practice != DEFAULT_MOCK_PRACTICE_ID:
        return False
    key = (_norm_name(first_name), _norm_name(last_name))
    return key in _VOICE_DEMO_FORCE_INCOMPLETE_PATIENTS


def apply_voice_demo_force_incomplete(
    canonical: dict[str, Any],
    *,
    first_name: Any = None,
    last_name: Any = None,
    practice_id: Any = None,
) -> bool:
    """
    Force ``deductible_remaining`` missing so routing falls through to INCOMPLETE
    and the voice gate has a recoverable target. No-op for non-demo patients.
    """
    if not is_voice_demo_force_incomplete_patient(
        first_name=first_name,
        last_name=last_name,
        practice_id=practice_id,
    ):
        return False

    missing = [
        str(f) for f in (canonical.get("missing_fields") or []) if f and str(f).strip()
    ]
    if _VOICE_DEMO_MISSING_FIELD not in missing:
        missing.append(_VOICE_DEMO_MISSING_FIELD)
    canonical["missing_fields"] = missing
    canonical["response_complete"] = False
    canonical["deductible_remaining"] = None

    warnings = list(canonical.get("integrity_warnings") or [])
    if _VOICE_DEMO_WARNING not in warnings:
        warnings.append(_VOICE_DEMO_WARNING)
    canonical["integrity_warnings"] = warnings
    return True
