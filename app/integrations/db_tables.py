"""Supabase public table names aligned with the hosted project schema."""

# ICD-10 dental crosswalk / GEM-style axis (not the greenfield `icd10_codes` seed table).
ICD10_DENTAL_GEM_AXIS = "icd10_dental_gem_axis"
CDT_CODES = "cdt_codes"
# Payer rules (public schema; formerly `coding_agent.payer_rules`).
PAYER_RULES = "payer_rules"
# Medicaid-style structured rules (requires_prior_auth, requires_report, age bands).
CDT_PAYER_RULES_STRUCTURED = "cdt_payer_rules_structured"
# Delta / handbook rows scoped for prior auth agent.
RULES_FOR_PREAUTH_AGENT_VIEW = "v_rules_for_preauth_agent"
# Generic agent trace rows (hosted project has no `agent_run_log`).
CODING_LOG = "coding_log"
