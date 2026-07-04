# PHI / Non-PHI Table Inventory

**Status:** Phase 0.2 complete (June 2026)  
**Source schema:** `supabase/migrations/000_baseline_production_schema.sql` (live-reconciled baseline, 2026-06-15)  
**Companion:** [vanguard-production-execution-plan.md](vanguard-production-execution-plan.md) Phase 0 · [database-operational-guide.md](database-operational-guide.md)  
**Next step:** Phase 0.3 — author Neon migrations from the **PORT** and **CREATE** rows below.

---

## How to read this inventory

| Column | Meaning |
| --- | --- |
| **Plane** | Target home after cutover |
| **Action** | What Phase 0.3+ does with the object |
| **Owner agent** | Which agent/workflow owns writes (per [agent-consolidation-roadmap.md](agent-consolidation-roadmap.md)) |
| **PHI signals** | Columns or payloads that force Neon placement |

### Plane values

| Plane | Provider | Rule |
| --- | --- | --- |
| **Neon (PHI)** | Neon Scale (BAA) | Patient/clinical/operational PHI; reachable only via FastAPI BFF + worker |
| **Supabase (non-PHI)** | Supabase Pro | Reference, rules, RAG vectors, clinic/payer directory, staff auth, de-identified evals |
| **Drop** | — | Remove from Supabase; do not port to Neon |
| **Neon (new)** | Neon Scale | Not in baseline; create in Phase 0.3 / 1 / 3 |

### Action values

| Action | Meaning |
| --- | --- |
| **PORT** | Copy table DDL + data migration path to Neon; remove from Supabase after cutover |
| **KEEP** | Stays on Supabase permanently |
| **DROP** | Delete from Supabase (demo / redundant) |
| **CREATE** | New table in Neon only (no Supabase counterpart) |
| **REPLACE** | View/RPC/trigger replaced by app-layer pattern (BFF, worker, or stays on Supabase) |

### Classification rules applied

1. **Tokenization does not make data non-PHI** — if we hold a re-identification key, it stays on Neon.
2. **Reference tables** (CDT/ICD catalogs, payer rules, fee schedules, embeddings) stay on Supabase even when they contain code descriptions — no patient linkage.
3. **`rcm.practices` + `rcm.provider_payer_network`** stay on Supabase — clinic/payer directory metadata, not patient records (no names/DOB/member IDs).
4. **`patient.providers`** ports to Neon — tied to clinical encounters in the PHI workflow (not a public provider directory product).
5. **Demo tables** (`legacy/044_*`) are **DROP** — dashboard already uses `demoData.ts`; 044 granted anon PHI and must not move to Neon.
6. **Bridge `public.*` views** over PHI tables are not ported as browser-facing views; Neon uses domain schemas + FastAPI. Reference bridge views stay on Supabase.

---

## Summary counts

| Plane / action | Tables | Views (excl. bridges) | Functions |
| --- | ---: | ---: | ---: |
| Neon — PORT | 24 | 1 | 1 |
| Neon — CREATE (new) | 4 | 0 | 0 |
| Supabase — KEEP | 16 | 6 | 1 |
| Drop | 4 | 0 | 0 |
| Replace (pattern change) | — | 1 read model + 40 bridge views | 2 triggers → worker |

**Baseline tables:** 36 physical tables across 7 domain schemas.  
**Legacy demo (044, not in baseline):** 4 tables → DROP.  
**Net-new Neon tables for pilot:** `pipeline_runs`, `user_practice_roles`, `sla_policies` (+ optional `task_sla` linkage).

---

## 1. Neon (PHI) — PORT

These tables move to Neon. After cutover, **remove from Supabase** and revoke all browser/anon access.

### 1.1 `patient` domain

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `patient.patients` | `patients` | PORT | *(external intake / future)* | `name`, `dob`, `insurance_id` | No in-repo writer today; must exist before claims FK |
| `patient.providers` | `providers` | PORT | *(external / seed)* | `full_name` | FK parent for encounters |
| `patient.encounters` | `encounters` | PORT | **Coding** | `clinical_note`, `procedures_json`, `attachments` | `decision_service` updates `status` |

### 1.2 `agents` domain (workflow + clinical artifacts)

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `agents.agent_decisions` | `agent_decisions` | PORT | **Coding** | `input_snapshot`, `reasoning`, `output` | Canonical coding decision store |
| `agents.rcm_tasks` | `rcm_tasks`, `agents.tasks` | PORT | **Workflow OS** | `patient_name`, `patient_dob`, `clinical_note`, `pipeline_json` | Zero writers today — central HITL queue |
| `agents.rcm_task_events` | `rcm_task_events`, `agents.task_events` | PORT | **Workflow OS** | `payload` (may contain PHI) | Append-only task audit / moat instrumentation |
| `agents.claim_intake_snapshot` | `claim_intake_snapshot` | PORT | **Claim** (read today) / external writer | `patient`, `subscriber`, `service_lines`, … jsonb | `encounter_id` is **text** — reconcile with `encounters.id` uuid |

### 1.3 `feedback` domain

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `feedback.decision_feedback` | `decision_feedback` | PORT | **Coding** (review flow) | `human_override`, `reason` | Moat: human overrides |

### 1.4 `audit` domain

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `audit.audit_logs` | `audit_logs`, `audit.audit_events` | PORT | **Platform** (unified writer, Phase 3) | `metadata`, `entity_id` | Schema-only today |

### 1.5 `logs` domain

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `logs.coding_log` | `coding_log` | PORT | **Coding** / n8n (retire external) | `clinical_note`, `patient_id`, wide code rows | Bring n8n writes in-house during Coding consolidation |
| `logs.eligibility_audit_log` | `eligibility_audit_log` | PORT | **Eligibility** | `patient_id`, `detail` (scrubbed but PHI-plane) | Keep scrub-before-insert |

### 1.6 `rcm` domain — eligibility (live)

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `rcm.eligibility_requests` | `eligibility_requests` | PORT | **Eligibility** | `first_name`, `last_name`, `dob`, `subscriber_id`, demographics | Queue moves to worker in Phase 3 |
| `rcm.eligibility_checks` | `eligibility_checks` | PORT | **Eligibility** | `raw_response` (271), financials | `raw_response` never exposed to browser post-cutover |
| `rcm.procedure_estimates` | `procedure_estimates` | PORT | **Eligibility** | linked to check | Layer 5 cost output |
| `rcm.eligibility_request_events` | `eligibility_request_events` | PORT | **Eligibility** (Edge Fn → worker) | request timeline | |
| `rcm.eligibility_agent_settings` | `eligibility_agent_settings` | PORT | **Eligibility** | none (operational config) | Singleton; no PHI columns but PHI-subsystem config |

### 1.7 `rcm` domain — claims, denials, agent runs

| Table | `public` view | Action | Owner agent | PHI signals | Notes |
| --- | --- | --- | --- | --- | --- |
| `rcm.agent_runs` | `agent_runs` | PORT | **Prior-Auth** (today); shared run-log target | `input_json`, `output_json` | Add lifecycle in PA consolidation |
| `rcm.claims` | `claims` | PORT | **Claim** | `cdt_lines`, `icd10_codes`, compliance fields | No writer today |
| `rcm.accepted_claims` | `accepted_claims`, `claim_submissions` | PORT | **Workflow OS + Claim** | `final_codes`, `source_pipeline_json` | No writer today |
| `rcm.denied_claims` | `denied_claims`, `denials` | PORT | **Denial** | denial analysis fields | Replace n8n writer |

---

## 2. Neon (PHI) — CREATE (not in baseline)

| Table | Action | Owner | Phase | Purpose |
| --- | --- | --- | --- | --- |
| `pipeline_runs` | CREATE | **Platform worker** | 0.3 skeleton / 3.1 full | Durable async pipeline + eligibility queue replacement |
| `user_practice_roles` | CREATE | **Platform auth** | 1.2 | RBAC: admin / billing_lead / front_office / read_only |
| `sla_policies` | CREATE | **Workflow OS** | 0.3 skeleton | SLA config for task types (research §5.5) |
| *(optional)* `practice_id` column | CREATE/ALTER | **Platform** | 1.3 | Add to every PORT table above — not a new table but required tenancy column |

> `agents.rcm_tasks` / `rcm_task_events` already exist — they are the Workflow OS task spine; `sla_policies` attaches to them rather than introducing duplicate `tasks` tables.

---

## 3. Supabase (non-PHI) — KEEP

Reference, rules, RAG, and clinic/payer directory. **No PHI-shaped columns.** CI must reject new migrations that add PHI columns here.

### 3.1 `analytics` domain (all KEEP)

| Table | `public` view | Owner | Notes |
| --- | --- | --- | --- |
| `analytics.rule_sources` | `rule_sources` | Reference seed | Provenance for rule ingest |
| `analytics.cdt_code_master` | `cdt_code_master` | Reference seed | Rule PDF short descriptions; FK parent for Medicaid rules |
| `analytics.cdt_codes` | `cdt_codes`, `cdt_codes_master` | Reference + embedding job | **pgvector** HNSW; `embed_cdt_jina_backfill.py` writes `embedding` |
| `analytics.icd10_codes` | `icd10_codes` | Reference seed | Full ICD-10-CM master |
| `analytics.icd10_dental_gem_axis` | `icd10_dental_gem_axis` | Reference seed (**gap: unseeded**) | Coding ICD validation — seed in Agent 2 consolidation |
| `analytics.codes` | `codes` | — | Schema-only legacy |
| `analytics.coding_rules` | `coding_rules` | — | Schema-only legacy |
| `analytics.hio_rules` | `hio_rules` | — | Schema-only legacy |

### 3.2 `agents` registry (KEEP)

| Table | `public` view | Notes |
| --- | --- | --- |
| `agents.agents` | `agents`, `agents.registry` | Agent registry; unused FK today |

### 3.3 `rcm` payer / rules reference (KEEP)

| Table | `public` view | Readers | Notes |
| --- | --- | --- | --- |
| `rcm.payer_network` | `payer_network` | Eligibility L0, PA, `payer_identity.py` | Stedi trading partner directory |
| `rcm.practices` | `practices` | Fee network FK | Clinic registry — **not patient PHI** |
| `rcm.provider_payer_network` | `provider_payer_network` | Eligibility cost calc | INN/OON per practice/NPI/payer |
| `rcm.payer_fee_schedules` | `payer_fee_schedules` | Eligibility L5 | Contracted fees |
| `rcm.payer_prior_auth_rules` | `payer_prior_auth_rules` | Eligibility router | **Unseeded** — seed or remove read |
| `rcm.payer_rules` | `payer_rules` | Coding, `match_cdt_codes` | Central payer rule table |
| `rcm.cdt_payer_rules` | `cdt_payer_rules` | — | Unstructured Medicaid rules; unused in Python |
| `rcm.cdt_payer_rules_structured` | `cdt_payer_rules_structured` | Prior-auth | Structured Medicaid rules |

### 3.4 Dormant RAG stack (KEEP on Supabase when enabled)

Not in baseline; created by `legacy/005_coding_agent_rag.sql`. Keep on non-PHI plane if activated.

| Table | Action | Notes |
| --- | --- | --- |
| `rag_documents` | KEEP | PDF ingest metadata |
| `rag_document_chunks` | KEEP | Chunk text + embeddings |
| `match_rag_chunks` RPC | KEEP | Not wired to agents today |

### 3.5 De-identified eval corpora (KEEP — future)

| Store | Plane | Notes |
| --- | --- | --- |
| `evals/*` golden datasets | Supabase or object storage | Non-PHI by construction |
| De-identified historical claims | Supabase | Safe Harbor ETL output only (Phase 5) |

---

## 4. DROP (do not port)

### 4.1 Legacy demo seed (`legacy/044_demo_rcm_modules_seed.sql`)

Applied in some environments; **not** in `000_baseline`. Drop on Supabase cleanup (Phase 1.4).

| Table | PHI signals | Reason |
| --- | --- | --- |
| `rcm.demo_coding_cases` | `patient_name`, `dob`, `clinical_note` | Dashboard uses `demoData.ts`; anon grants were unsafe |
| `rcm.demo_prior_auth_cases` | `patient_name`, `dob` | Same |
| `rcm.demo_claims` | patient fields | Same |
| `rcm.demo_denials` | patient fields | Same |

---

## 5. Views & RPCs

### 5.1 Neon — PORT or REPLACE

| Object | Type | Action | Replacement |
| --- | --- | --- | --- |
| `public.eligibility_dashboard_rows` | view | REPLACE | FastAPI BFF list endpoint; no browser SQL |
| `public.get_claim_intake_snapshot(...)` | RPC | PORT | FastAPI route or Neon SQL in claim agent |
| 40× `public.*` bridge views over PORT tables | view | DROP on Supabase | Neon: app uses domain schemas directly; no `public` anon bridges |

### 5.2 Supabase — KEEP

| Object | Type | Used by |
| --- | --- | --- |
| `public.v_rules_for_coding_agent` | view | Coding agent projection |
| `public.v_rules_for_preauth_agent` | view | Prior-auth |
| `public.v_rules_for_estimation_agent` | view | Reserved |
| `public.v_rules_for_scrubber_agent` | view | Reserved |
| `public.v_rules_for_appeals_agent` | view | Denial (unwired) |
| `public.v_cdt_code_exclusions` | view | Rule joins |
| `public.match_cdt_codes(...)` | RPC | `cdt_vector_memory.py` — **fix `billing_ exclusion` typo** in Agent 2 |
| 16× bridge views over KEEP tables | view | supabase-py reference reads |

### 5.3 Triggers & edge queue — REPLACE (Phase 3)

| Object | Current | Target |
| --- | --- | --- |
| `rcm.invoke_eligibility_request_processor()` | DB trigger → Edge Fn → agent | Neon `pipeline_runs` worker polls / claims jobs |
| `trg_process_eligibility_request` | AFTER INSERT on `eligibility_requests` | Worker enqueue |
| `trg_retry_eligibility_request` | AFTER UPDATE → `queued` | Worker / `retry_worker` on Neon |
| Edge Fn `process-eligibility-request` | Supabase | Retire after worker cutover |

Interim: eligibility can run on Neon with a ported trigger **or** in-process worker until Phase 3 lands — prefer worker to avoid Supabase Edge dependency on PHI plane.

---

## 6. Cross-plane read matrix (after cutover)

FastAPI holds **both** connection strings; PHI code paths must not import Supabase client for PORT tables.

| Consumer | Neon (PHI) | Supabase (non-PHI) |
| --- | --- | --- |
| `app/eligibility/db.py` | requests, checks, estimates, events, settings, eligibility_audit_log | `payer_network`, `payer_fee_schedules`, `provider_payer_network`, `payer_prior_auth_rules`, `cdt_codes` |
| `app/services/decision_service.py` | encounters, agent_decisions, decision_feedback | — |
| `app/tools/coding_tools.py` | agent_decisions (post-consolidation) | `cdt_codes`, `icd10_dental_gem_axis`, `payer_rules`, `match_cdt_codes` |
| `app/integrations/agent_runs.py` | agent_runs | `cdt_payer_rules_structured`, `payer_network` |
| `app/agents/rcm_pipeline.py` | claim_intake_snapshot, tasks (future) | coding/PA reference reads |
| `eligibility_dashboard` (browser) | **none** — BFF only | Supabase Auth JWT only |

---

## 7. Agent ownership map (for 0.3 migration grouping)

Group Neon migrations by agent to match [agent-consolidation-roadmap.md](agent-consolidation-roadmap.md) PR cadence.

| Agent / area | PORT tables | CREATE tables |
| --- | --- | --- |
| **Platform** | `audit_logs`, `pipeline_runs` (skeleton) | `pipeline_runs`, `user_practice_roles`, `sla_policies` |
| **Eligibility** | `eligibility_*`, `procedure_estimates`, `eligibility_audit_log`, `eligibility_agent_settings` | — |
| **Coding** | `encounters`, `agent_decisions`, `decision_feedback`, `coding_log` | — |
| **Prior-Auth** | `agent_runs` | — |
| **Workflow OS** | `rcm_tasks`, `rcm_task_events`, `accepted_claims` | `sla_policies` |
| **Claim** | `claims`, `claim_intake_snapshot` | — |
| **Denial** | `denied_claims` | — |
| **Patient spine** | `patients`, `providers` | — |

**Suggested 0.3 migration order:**

1. `neon/001_platform_core.sql` — `patients`, `providers`, `encounters`, `audit_logs`, `pipeline_runs` (empty skeleton), `practice_id` columns
2. `neon/002_eligibility.sql` — eligibility tables + settings + eligibility_audit_log
3. `neon/003_agents_workflow.sql` — `agent_decisions`, `agent_runs`, `rcm_tasks`, `rcm_task_events`, `sla_policies`
4. `neon/004_claims_denials.sql` — `claims`, `accepted_claims`, `denied_claims`, `claim_intake_snapshot`, `coding_log`

---

## 8. Phase 0.2 checklist

- [x] Classify every baseline table as PHI-plane or non-PHI-plane
- [x] Reference/rules/RAG/payer/fee tables → Supabase (KEEP)
- [x] Eligibility, tasks, agent_runs/decisions, claim snapshots, claims, denials, audit → Neon (PORT)
- [x] Demo tables (044) → DROP (do not port)
- [x] Document inventory: table → plane → owner agent (this file)
- [x] Author Neon DDL `neon/migrations/001`–`004` (Phase 0.3)
- [x] Apply migrations to Neon project (Vanguard `dry-night-19725046`, 2026-06-17)
- [ ] Review sign-off (engineering) before Phase 2 asyncpg cutover
- [x] De-identification ETL skeleton (`app/compliance/deidentification.py`) + CI guard (`scripts/check_supabase_migrations_phi_columns.py`)

---

## 9. Open decisions (resolve before 0.3)

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | Port `patient.providers` to Neon or keep as Supabase reference? | **Neon** — encounter FK + clinical workflow |
| 2 | Keep `eligibility_agent_settings` on Neon? | **Yes** — eligibility subsystem config |
| 3 | Merge `logs.eligibility_audit_log` into `audit.audit_logs`? | **Defer** — port both; unify writer in Phase 3 |
| 4 | Create duplicate `tasks` table vs use `rcm_tasks`? | **Use `rcm_tasks`** — already in baseline |
| 5 | `rag_documents` stack | **KEEP Supabase** if enabled; not pilot-critical |

### Resolved in 0.3 DDL

| # | Decision | Resolution |
| --- | --- | --- |
| 1–5 | See above | Implemented in `neon/migrations/001`–`004` |
| 6 | `eligibility_agent_settings` shape | **Per-practice row** (`practice_id` PK) instead of global singleton — required for multi-clinic tenancy |

---

*Generated for Phase 0.2. Reconcile after any baseline schema change or new forward migration `046_+`.*
