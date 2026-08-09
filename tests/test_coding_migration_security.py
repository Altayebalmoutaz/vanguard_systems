"""Regression tests for coding-run PHI database permissions."""

from __future__ import annotations

import re
from pathlib import Path


def _normalized_sql(path: str) -> str:
    sql = Path(path).read_text(encoding="utf-8").lower()
    return re.sub(r"\s+", " ", sql)


def test_coding_tables_are_not_initially_granted_to_browser_roles() -> None:
    for migration in (
        "supabase/migrations/059_coding_runs.sql",
        "supabase/migrations/061_coding_decisions.sql",
    ):
        sql = _normalized_sql(migration)
        assert " to authenticated" not in sql
        assert " to anon" not in sql


def test_forward_migration_revokes_all_browser_role_access() -> None:
    sql = _normalized_sql("supabase/migrations/064_lock_down_coding_phi.sql")
    relations = (
        "agents.coding_runs",
        "agents.coding_decisions",
        "public.coding_runs",
        "public.coding_decisions",
    )

    for relation in relations:
        assert (
            f"revoke all privileges on table {relation} "
            "from public, anon, authenticated;"
        ) in sql
        assert (
            f"grant select, insert, update, delete on table {relation} "
            "to service_role;"
        ) in sql
