#!/usr/bin/env python3
"""
CI guard: forward Supabase migrations must not introduce PHI-shaped schema.

Scans ``supabase/migrations/*.sql`` with numeric prefix >= 045 (see
``supabase/migrations/README.md``). The reconciled ``000_baseline_*`` and
``legacy/`` tree are excluded — baseline PHI on Supabase predates the plane split.

Exits 0 when clean, 1 when forbidden DDL is detected.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
FORWARD_MIN_PREFIX = 45

# Column names that must not appear in new Supabase (non-PHI) migrations.
FORBIDDEN_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "patient_name",
        "first_name",
        "last_name",
        "full_name",
        "dob",
        "date_of_birth",
        "patient_dob",
        "subscriber_id",
        "member_id",
        "insurance_id",
        "ssn",
        "social_security_number",
        "mbi",
        "raw_response",
        "clinical_note",
        "raw_note",
        "phone",
        "phone_number",
        "email",
        "email_address",
        "address",
        "street",
        "street_address",
        "postal_code",
        "zip_code",
        "demographics_block",
        "input_snapshot",
        "pipeline_json",
        "source_pipeline_json",
    }
)

# Whole tables that must not be created on the Supabase forward path.
FORBIDDEN_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "patients",
        "encounters",
        "providers",
        "eligibility_requests",
        "eligibility_checks",
        "eligibility_request_events",
        "procedure_estimates",
        "claims",
        "accepted_claims",
        "denied_claims",
        "agent_decisions",
        "agent_runs",
        "rcm_tasks",
        "rcm_task_events",
        "claim_intake_snapshot",
        "decision_feedback",
        "audit_logs",
        "coding_log",
        "eligibility_audit_log",
        "pipeline_runs",
        "user_practice_roles",
        "demo_coding_cases",
        "demo_prior_auth_cases",
        "demo_claims",
        "demo_denials",
    }
)

_MIGRATION_PREFIX_RE = re.compile(r"^(\d{3})_")
_STRIP_COMMENTS_RE = re.compile(
    r"--[^\n]*|/\*.*?\*/",
    re.DOTALL,
)
_CREATE_TABLE_RE = re.compile(
    r"create\s+table(?:\s+if\s+not\s+exists)?\s+"
    r"(?:(\w+)\.)?(\w+)\s*\(",
    re.IGNORECASE,
)
_ADD_COLUMN_RE = re.compile(
    r"alter\s+table\s+(?:(\w+)\.)?(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)",
    re.IGNORECASE,
)
_COLUMN_DEF_RE = re.compile(
    r"^\s*(\w+)\s+",
    re.IGNORECASE | re.MULTILINE,
)


def _migration_prefix(path: Path) -> int | None:
    match = _MIGRATION_PREFIX_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1))


def forward_migration_files(migrations_dir: Path | None = None) -> list[Path]:
    root = migrations_dir or MIGRATIONS_DIR
    files = [
        p
        for p in root.glob("*.sql")
        if p.is_file() and (_migration_prefix(p) or 0) >= FORWARD_MIN_PREFIX
    ]
    return sorted(files, key=lambda p: p.name)


def strip_sql_comments(sql: str) -> str:
    return _STRIP_COMMENTS_RE.sub(" ", sql)


def find_violations(path: Path) -> list[str]:
    sql = strip_sql_comments(path.read_text(encoding="utf-8"))
    violations: list[str] = []

    for match in _CREATE_TABLE_RE.finditer(sql):
        schema, table = match.group(1), match.group(2)
        table_lc = table.lower()
        if table_lc in FORBIDDEN_TABLE_NAMES:
            qual = f"{schema}.{table}" if schema else table
            violations.append(f"{path.name}: create table {qual} (PHI-plane table)")

        # Column list between this match and the closing paren at table level — heuristic:
        start = match.end()
        depth = 1
        idx = start
        while idx < len(sql) and depth > 0:
            ch = sql[idx]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            idx += 1
        body = sql[start : idx - 1]
        for col_match in _COLUMN_DEF_RE.finditer(body):
            col = col_match.group(1).lower()
            if col in {"primary", "unique", "check", "constraint", "foreign", "exclude"}:
                continue
            if col in FORBIDDEN_COLUMN_NAMES:
                violations.append(
                    f"{path.name}: column {col!r} in create table {table_lc}"
                )

    for match in _ADD_COLUMN_RE.finditer(sql):
        schema, table, column = match.group(1), match.group(2), match.group(3)
        col = column.lower()
        if col in FORBIDDEN_COLUMN_NAMES:
            qual = f"{schema}.{table}" if schema else table
            violations.append(f"{path.name}: alter table {qual} add column {col!r}")

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help="Supabase migrations directory (default: supabase/migrations)",
    )
    args = parser.parse_args(argv)

    migrations_dir: Path = args.migrations_dir
    files = forward_migration_files(migrations_dir)
    if not files:
        print(f"no forward migrations (>={FORWARD_MIN_PREFIX:03}) under {migrations_dir}")
        return 0

    all_violations: list[str] = []
    for path in files:
        all_violations.extend(find_violations(path))

    if all_violations:
        print("Supabase forward migration PHI guard FAILED:", file=sys.stderr)
        for item in all_violations:
            print(f"  - {item}", file=sys.stderr)
        return 1

    checked = ", ".join(p.name for p in files)
    print(f"Supabase forward migration PHI guard OK ({checked})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
