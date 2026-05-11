"""Universal dental semantic record (v1) built from Layer 3 canonical."""

from app.eligibility.universal_dental.build import build_universal_dental_record
from app.eligibility.universal_dental.models import UniversalDentalRecord

__all__ = ["UniversalDentalRecord", "build_universal_dental_record"]
