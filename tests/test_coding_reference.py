"""Wave 1D coding reference-plane consolidation regression tests."""

from __future__ import annotations

from pathlib import Path

from scripts.check_supabase_migrations_phi_columns import find_violations

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
BASELINE = MIGRATIONS / "000_baseline_production_schema.sql"
FIX_MIGRATION = MIGRATIONS / "051_fix_match_cdt_codes_billing_exclusion.sql"
SEED_MIGRATION = MIGRATIONS / "052_seed_icd10_dental_gem_axis.sql"

BILLING_EXCLUSION_TYPO = "billing_ exclusion"
BILLING_EXCLUSION_CORRECT = "billing_exclusion"
ICD_NONEMPTY_FN = "check_icd10_dental_gem_axis_nonempty"
REQUIRED_ICD_CODES = ("K02.9", "K04.0")


def _match_cdt_codes_function_body(sql: str) -> str:
    marker = "create or replace function public.match_cdt_codes"
    start = sql.lower().find(marker)
    assert start >= 0, "match_cdt_codes definition not found"
    tail = sql[start:]
    open_delim = tail.lower().find("as $$")
    assert open_delim >= 0, "match_cdt_codes AS $$ block not found"
    close_delim = tail.find("$$;", open_delim + 4)
    assert close_delim >= 0, "match_cdt_codes closing $$ not found"
    return tail[open_delim:close_delim]


def test_baseline_match_cdt_codes_has_billing_exclusion_typo() -> None:
    """Documents the baseline bug fixed by migration 051."""
    body = _match_cdt_codes_function_body(BASELINE.read_text(encoding="utf-8"))
    assert BILLING_EXCLUSION_TYPO in body
    assert f"r.rule_type='{BILLING_EXCLUSION_CORRECT}'" not in body


def test_051_fixes_match_cdt_codes_billing_exclusion_typo() -> None:
    body = _match_cdt_codes_function_body(FIX_MIGRATION.read_text(encoding="utf-8"))
    assert BILLING_EXCLUSION_TYPO not in body
    assert f"r.rule_type='{BILLING_EXCLUSION_CORRECT}'" in body


def test_052_defines_icd_table_nonempty_check_function() -> None:
    sql = SEED_MIGRATION.read_text(encoding="utf-8")
    assert f"function public.{ICD_NONEMPTY_FN}()" in sql.lower()
    assert "returns boolean" in sql.lower()
    assert "from analytics.icd10_dental_gem_axis" in sql


def test_052_seeds_required_dental_icd_codes() -> None:
    sql = SEED_MIGRATION.read_text(encoding="utf-8")
    for code in REQUIRED_ICD_CODES:
        assert code in sql


def test_051_and_052_pass_phi_guard() -> None:
    assert find_violations(FIX_MIGRATION) == []
    assert find_violations(SEED_MIGRATION) == []
