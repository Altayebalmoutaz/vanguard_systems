"""Smoke-test Neon PHI schema: table inventory, RLS, constraints, helper RPC."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    ("platform", "user_practice_roles"),
    ("platform", "pipeline_runs"),
    ("platform", "sla_policies"),
    ("patient", "patients"),
    ("patient", "providers"),
    ("patient", "encounters"),
    ("audit", "audit_logs"),
    ("logs", "eligibility_audit_log"),
    ("logs", "coding_log"),
    ("rcm", "eligibility_checks"),
    ("rcm", "eligibility_requests"),
    ("rcm", "eligibility_request_events"),
    ("rcm", "procedure_estimates"),
    ("rcm", "eligibility_agent_settings"),
    ("rcm", "agent_runs"),
    ("rcm", "claims"),
    ("rcm", "accepted_claims"),
    ("rcm", "denied_claims"),
    ("agents", "rcm_tasks"),
    ("agents", "rcm_task_events"),
    ("agents", "agent_decisions"),
    ("agents", "claim_intake_snapshot"),
    ("feedback", "decision_feedback"),
}

PRACTICE_A = "smoke_practice_a"
PRACTICE_B = "smoke_practice_b"


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    url = os.getenv("NEON_DATABASE_URL")
    if not url:
        raise SystemExit("NEON_DATABASE_URL is not set in .env")

    failures: list[str] = []

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_schema in (
                  'platform', 'patient', 'audit', 'logs', 'rcm', 'agents', 'feedback'
                )
                and table_type = 'BASE TABLE'
                order by 1, 2
                """
            )
            found = {(row[0], row[1]) for row in cur.fetchall()}
            missing = EXPECTED_TABLES - found
            extra = found - EXPECTED_TABLES
            if missing:
                failures.append(f"missing tables: {sorted(missing)}")
            if extra:
                print(f"note: extra tables present: {sorted(extra)}")
            print(f"tables ok ({len(EXPECTED_TABLES)} expected)")

            cur.execute(
                """
                select count(*) from pg_policies
                where schemaname in (
                  'platform', 'patient', 'audit', 'logs', 'rcm', 'agents', 'feedback'
                )
                """
            )
            policy_count = cur.fetchone()[0]
            if policy_count < 20:
                failures.append(f"expected >= 20 RLS policies, found {policy_count}")
            else:
                print(f"rls policies present ({policy_count})")

            cur.execute(
                """
                select rolsuper, rolbypassrls
                from pg_roles
                where rolname = current_user
                """
            )
            is_superuser, bypasses_rls = cur.fetchone()
            is_superuser = bool(is_superuser)
            bypasses_rls = bool(bypasses_rls)

            if is_superuser or bypasses_rls:
                print(
                    "note: connect role bypasses RLS — skipping live enforcement test "
                    "(create a non-bypass app role for production; see neon/migrations/README.md)"
                )
            else:
                cur.execute("savepoint rls_block_test")
                try:
                    cur.execute(
                        """
                        insert into patient.patients (practice_id, name)
                        values (%s, %s)
                        """,
                        (PRACTICE_A, "RLS Block Test"),
                    )
                    failures.append("RLS did not block insert without app.practice_id")
                except psycopg.Error:
                    print("rls blocks insert without session GUC")
                finally:
                    cur.execute("rollback to savepoint rls_block_test")

            # Seed path uses bypass for superuser-safe setup
            cur.execute("set local app.bypass_rls = 'true'")
            patient_id = uuid.uuid4()
            cur.execute(
                """
                insert into patient.patients (id, practice_id, name, dob)
                values (%s, %s, %s, %s)
                """,
                (patient_id, PRACTICE_A, "Smoke Patient", "1990-01-15"),
            )
            cur.execute(
                """
                insert into rcm.eligibility_agent_settings (practice_id)
                values (%s)
                on conflict (practice_id) do nothing
                """,
                (PRACTICE_A,),
            )
            cur.execute(
                """
                insert into rcm.eligibility_requests (
                  practice_id, patient_id, first_name, last_name, dob,
                  subscriber_id, primary_payer_id, status
                ) values (%s, %s, %s, %s, %s, %s, %s, 'queued')
                returning id
                """,
                (
                    PRACTICE_A,
                    patient_id,
                    "Smoke",
                    "Patient",
                    "1990-01-15",
                    "SUB123",
                    "84103",
                ),
            )
            request_id = cur.fetchone()[0]
            print(f"seeded eligibility_request {request_id}")

            # agent_runs status constraint
            cur.execute("savepoint status_check")
            try:
                cur.execute(
                    """
                    insert into rcm.agent_runs (practice_id, agent, status)
                    values (%s, %s, %s)
                    """,
                    (PRACTICE_A, "prior_auth", "not_a_real_status"),
                )
                failures.append("agent_runs status CHECK did not fire")
            except psycopg.Error:
                print("agent_runs status check ok")
            finally:
                cur.execute("rollback to savepoint status_check")

            # claim intake helper
            cur.execute(
                """
                insert into agents.claim_intake_snapshot (
                  practice_id, encounter_id, ready_for_claim, intake_status, patient
                ) values (%s, %s, true, 'ready', %s::jsonb)
                on conflict (practice_id, encounter_id) do update
                  set ready_for_claim = excluded.ready_for_claim
                """,
                (PRACTICE_A, "ENC-SMOKE-1", '{"name": "Smoke Patient"}'),
            )
            cur.execute(
                "select agents.get_claim_intake_snapshot(%s, %s)",
                (PRACTICE_A, "ENC-SMOKE-1"),
            )
            snapshot = cur.fetchone()[0]
            if not snapshot or snapshot.get("encounter_id") != "ENC-SMOKE-1":
                failures.append("get_claim_intake_snapshot returned unexpected payload")
            else:
                print("claim_intake_snapshot RPC ok")

            # Tenant isolation (meaningful only for non-superuser roles)
            if not is_superuser and not bypasses_rls:
                cur.execute("set local app.bypass_rls = 'false'")
                cur.execute(f"set local app.practice_id = '{PRACTICE_A}'")
                cur.execute("select count(*) from patient.patients")
                count_a = cur.fetchone()[0]
                cur.execute(f"set local app.practice_id = '{PRACTICE_B}'")
                cur.execute("select count(*) from patient.patients")
                count_b = cur.fetchone()[0]
                if count_a < 1 or count_b != 0:
                    failures.append(
                        f"tenant scoping failed: practice_a={count_a}, practice_b={count_b}"
                    )
                else:
                    print("tenant scoping via app.practice_id ok")

        conn.rollback()

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        raise SystemExit(1)

    print("smoke test passed")


if __name__ == "__main__":
    main()
