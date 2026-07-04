"""Tests for Supabase forward-migration PHI column guard."""

from __future__ import annotations

from pathlib import Path

from scripts.check_supabase_migrations_phi_columns import (
    find_violations,
    forward_migration_files,
    main,
)


def test_forward_migrations_include_045() -> None:
    names = [p.name for p in forward_migration_files()]
    assert "045_eligibility_consolidation.sql" in names
    assert not any(n.startswith("000_") for n in names)


def test_045_passes_phi_guard() -> None:
    path = Path("supabase/migrations/045_eligibility_consolidation.sql")
    assert find_violations(path) == []


def test_detects_forbidden_column_in_synthetic_migration(tmp_path: Path) -> None:
    bad = tmp_path / "046_bad_phi.sql"
    bad.write_text(
        """
        create table if not exists analytics.eval_cases (
          id uuid primary key,
          patient_name text not null
        );
        """,
        encoding="utf-8",
    )
    violations = find_violations(bad)
    assert any("patient_name" in v for v in violations)


def test_main_exits_zero_on_repo_migrations() -> None:
    assert main([]) == 0
