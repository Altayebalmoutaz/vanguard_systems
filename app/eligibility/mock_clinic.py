"""Synthetic demo clinic identifiers aligned with migration ``040_mock_clinic_practices_and_seed``.

Use optional ``practice_id`` / ``rendering_provider_npi`` on ``EligibilityRequest``
with seeded ``provider_payer_network`` rows so Layer 5 uses directory-based fee path.
"""

DEFAULT_MOCK_PRACTICE_ID = "vgd_mock_brooklyn"
DEFAULT_MOCK_RENDERING_NPI = "1104023674"
DEFAULT_MOCK_LOCATION_KEY = "site_main"

# Seeded in migration 041 (vgd_mock_brooklyn + NPI 1104023674): 84103, AMTAS00425, 62308, 10134,
# 52133, 60054, 77777 (site_main), 64246 (OON for regression). Associate NPI 1982654321 + 62308.
