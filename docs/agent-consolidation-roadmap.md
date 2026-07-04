# Agent Database Consolidation Roadmap

**Status:** Active plan · companion to [database-operational-guide.md](database-operational-guide.md)
**Purpose:** Rate each agent's **database/data-layer maturity** and define a repeatable, one-agent-at-a-time consolidation process. Going forward we consolidate **per agent**: each agent gets a clean owned data contract, a single writer, closed schema↔code gaps, and a forward migration.

> "Consolidate an agent" here means: lock down **which tables that agent owns**, make the
> **code and schema agree**, implement the **missing persistence / state machine**, fix
> **security/plane** issues, and ship it as a focused forward migration (`045_+`) plus the
> wiring + tests. One agent per PR.

---

## 1. Scoring rubric

Each agent is scored 0–5 on six operational dimensions:

| Dim | Question |
| --- | --- |
| **SF** Schema fidelity | Do the tables the code uses exist and match what the code writes/reads? |
| **PD** Persistence & durability | Are results actually stored (vs. returned over HTTP and lost)? |
| **SM** State lifecycle | Is there a real status machine with implemented transitions? |
| **OW** Ownership clarity | Is there a single, obvious writer per table (no split/external surprises)? |
| **RR** Reference/read-path readiness | Is the reference data it reads actually seeded and correct? |
| **SP** Security & plane fit | RLS posture, PHI handling, PHI-plane vs non-PHI-plane alignment? |

Overall grade is a holistic operational-readiness call, not a raw average (security and durability are weighted heavily because they block production).

---

## 2. Scorecard

| Agent | SF | PD | SM | OW | RR | SP | Overall | One-line verdict |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| **Eligibility** | 5 | 5 | 5 | 3 | 4 | 3 | **B+ (consolidated — migration 045 + retry worker)** | HMAC signing + retry worker + raw-271 PHI revoke shipped; remaining: full RLS/tenant scoping (auth-coupled) and the Neon plane split. |
| **Coding** | 3 | 3 | 4 | 2 | 2 | 2 | **C (works, fragmented)** | Two persistence paths, unseeded ICD validation table, RPC bug, demo-only UI. |
| **Prior-Auth** | 4 | 3 | 2 | 4 | 4 | 4 | **C+ (clean but inert)** | Cleanly owns `agent_runs` with good server-only posture; no lifecycle after insert. |
| **Claim / Claim-draft** | 1 | 1 | 1 | 1 | 2 | 3 | **D (schema-only)** | Returns a draft over HTTP; persists nothing; `claims`/`accepted_claims` have no writer. |
| **Denial / Appeals** | 2 | 1 | 1 | 1 | 1 | 3 | **D− (externally owned)** | Agent returns JSON; `denied_claims` is written by production n8n, not the app. |
| *(Biller Workflow OS — cross-cutting)* | 2 | 0 | 0 | 0 | — | 3 | **F (unbuilt)** | `rcm_tasks`/`rcm_task_events`/`accepted_claims` + `pipeline_json` have zero writers. |

---

## 3. Per-agent assessment & consolidation approach

### 3.1 Eligibility agent — Grade B

**Owns:** `rcm.eligibility_requests`, `rcm.eligibility_checks`, `rcm.procedure_estimates`, `rcm.eligibility_request_events`, `rcm.eligibility_agent_settings`, `logs.eligibility_audit_log`, read-model `public.eligibility_dashboard_rows`.

**Strengths:** real normalized result model, full persistence (checks + estimates + events + audit), live dashboard with realtime, idempotency key, classified failures.

**Operational problems:**
- **Writer is split four ways** (browser INSERT, DB trigger, Edge Function UPDATE, agent INSERT) — correct by design but fragile, and the **HMAC mismatch** (signed Edge Function vs. unsigned 033-era trigger) will strand requests at `queued`.
- **No auto-retry scheduler**; `retrying` is a dead-end state; `auto_retry_enabled`/`auto_check_enabled` are never read.
- **PHI exposure:** `eligibility_checks.raw_response` + demographics readable by `anon`; RLS disabled on `eligibility_checks`/`procedure_estimates`.
- `payer_prior_auth_rules` read but unseeded.

**Consolidation status — SHIPPED (migration 045 + retry worker):**
1. ✅ **Webhook posture aligned.** `045_eligibility_consolidation.sql` re-introduces the signed dispatcher (HMAC-SHA256 `X-Webhook-Signature`, formerly 038) matching the deployed Edge Function. Prereq: Vault secret `eligibility_dashboard_edge_function_signing_secret` = function `WEBHOOK_SECRET`; the trigger fails the row with a clear `config_error` if it's absent.
2. ✅ **Retry worker built.** `app/eligibility/retry_worker.py` re-queues due `retrying` rows and exhausts past `max_attempts`, gated by the live `auto_retry_enabled` toggle. In-process FastAPI background task (`ELIGIBILITY_RETRY_WORKER_ENABLED`), wired into `app/main.py` lifespan, with unit tests.
3. ✅ **Raw-271 PHI revoked (non-breaking).** 045 revokes anon `SELECT` on `eligibility_checks` and blanks `raw_response` from the anon `eligibility_dashboard_rows` view (view runs with owner privileges → dashboard unaffected).

**Remaining (deferred — auth-coupled / future phase):**
4. **Full RLS-deny + tenant scoping** (legacy/037: revoke anon on the rest, `created_by = auth.uid()`) — ships **with Supabase Auth on the dashboard** (Phase 1); applying now would break the login-less anon UI.
5. **Neon plane split** for the eligibility PHI tables (read via BFF, not browser anon).
6. Seed `payer_prior_auth_rules` (or remove the dead read).

### 3.2 Coding agent — Grade C

**Owns (intended):** `agents.agent_decisions`, `feedback.decision_feedback`, and `logs.coding_log`. **Reads:** `analytics.cdt_codes` (+embedding), `analytics.icd10_dental_gem_axis`, `rcm.payer_rules`, RPC `match_cdt_codes`.

**Operational problems:**
- **Two disjoint persistence paths:** `/run-coding-agent` → `agent_decisions`; the `rcm_pipeline` path persists nothing. Harness writes thin `coding_log` rows; **production n8n writes the wide `coding_log` rows.** No single source of truth for "what the coding agent decided."
- **`icd10_dental_gem_axis` has no seed migration** but is the ICD validator → likely silent mis-validation.
- **`match_cdt_codes` RPC typo** (`'billing_ exclusion'`) → that rule bucket is always empty.
- `cdt_codes` vs `cdt_code_master` duplication with no sync.
- UI is demo-only.

**Consolidation approach:**
1. **Pick one canonical coding record.** Recommend `agent_decisions` as the durable store for *every* coding run (including the pipeline path), and demote `coding_log` to a trace/integration sink (document that its wide shape is n8n-owned, or bring that ingestion in-house).
2. **Seed `icd10_dental_gem_axis`** via a generator + `045_` migration; add a test that fails if the validator table is empty.
3. **Fix `match_cdt_codes`** (`billing_exclusion`) and add a regression test asserting non-empty buckets on seeded data.
4. Decide the `cdt_codes`/`cdt_code_master` relationship (merge, or document the split + add a consistency check).
5. PHI: `agent_decisions.input_snapshot`/`encounters` carry clinical notes → Neon-plane candidate.

### 3.3 Prior-Auth agent — Grade C+

**Owns:** `rcm.agent_runs`. **Reads:** `rcm.cdt_payer_rules_structured`, `public.v_rules_for_preauth_agent`, `rcm.payer_network`.

**Strengths:** cleanest ownership of any agent — one writer (`agent_runs.py`), **server-role-only grants** (good posture), seeded structured rules, real payer resolution.

**Operational problems:**
- **No lifecycle:** every run is inserted `status='pending_review'` and never updated — no approve/deny/expire transition, so `agent_runs` is write-only history with no resolution signal.
- `agent_runs` is generic ("agent" column) — it's really a shared run-log, not PA-specific; risks becoming a junk drawer.

**Consolidation approach:**
1. **Define the `agent_runs` state machine** (`pending_review → approved | denied | expired | superseded`) and implement the update path (likely via the review/HITL endpoint).
2. **Clarify scope:** either formally make `agent_runs` the *shared* agent run-log (and document that coding/claim should also write it) or split a PA-specific table. Recommend the former — it's the natural home for the "every agent decision lands in an audit row" moat requirement.
3. Add a CHECK constraint on `status`.
4. PHI: keep server-only; `input_json`/`output_json` may hold patient context → Neon-plane candidate. Migration `045_` adds the status CHECK + indexes for the resolution query.

### 3.4 Claim / Claim-draft agent — Grade D

**Owns (intended):** `rcm.claims`, `rcm.accepted_claims`. **Reads:** `agents.claim_intake_snapshot` (written externally).

**Operational problems:**
- **Persists nothing.** Builds a draft, optionally submits to Stedi, returns it. `rcm.claims` (with its `compliance_*` columns) and `rcm.accepted_claims` have **no writer**.
- Depends on `claim_intake_snapshot`, which has **no in-repo writer and no lifecycle code** (front-desk app owns it), and the Next.js proxy doesn't even pass `encounter_id`.

**Consolidation approach (largest build):**
1. **Make the claim agent persist.** On draft build → write `rcm.claims` (status `draft`, compliance verdict populated). On biller submit → write `rcm.accepted_claims` (final codes, `source_pipeline_json`).
2. **Resolve the intake snapshot ownership:** either build the snapshot writer/lifecycle in-repo (`draft→ready→submitted→archived`) or formally treat it as an external contract and validate it on read. Reconcile the **two encounter-id spaces** (`encounters.id` uuid vs `claim_intake_snapshot.encounter_id` text).
3. This agent is entangled with the **Workflow OS** tables (§3.6) — consolidate them together.

### 3.5 Denial / Appeals agent — Grade D−

**Owns (intended):** `rcm.denied_claims`. **Reads:** nothing from the rules plane (`v_rules_for_appeals_agent` is unused).

**Operational problems:** the app agent returns JSON and **writes nothing**; in production an **n8n flow** populates `denied_claims` via a hardcoded ngrok webhook (excluded from the baseline). So the "denial data" is owned by an external automation, not the app.

**Consolidation approach:**
1. **Bring denial persistence in-house:** the agent should write `rcm.denied_claims` directly (status machine `pending → in_appeal → resolved/abandoned`), replacing the n8n insert path. Decide n8n's fate (retire, or make it call the app instead of the DB).
2. Wire `v_rules_for_appeals_agent` into the appeal-letter logic (currently a static template).
3. This is the **lowest-ROI** until real ERA/835 data exists (Phase 5) — consolidate the *schema + writer* now, defer richness.

### 3.6 Biller Workflow OS (cross-cutting) — Grade F

`agents.rcm_tasks`, `agents.rcm_task_events`, `rcm.accepted_claims`, and the `pipeline_json`/`source_pipeline_json` blobs are **schema-only with zero writers**. This is the spine the Coding/PA/Claim agents are supposed to feed (one task per pipeline run → human review → accept). It blocks every RCM dashboard module (all on `demoData.ts`). **Consolidate this alongside the Claim agent**, since it's the shared persistence layer the per-agent outputs flow into.

---

## 4. Recommended consolidation order

Sequenced by ROI and dependency:

1. **Eligibility (finish it).** It's live and revenue-relevant — close HMAC + retry + RLS/PHI. Highest ROI, lowest build.
2. **Coding (de-fragment).** Seed `icd10_dental_gem_axis`, fix the RPC, pick one persistence record. Unblocks accurate coding + RAG.
3. **Prior-Auth (add lifecycle).** Small, and it defines the shared `agent_runs` audit pattern the others reuse.
4. **Workflow OS + Claim (build the spine).** Largest effort; makes the biller queue + claims real and lights up the demo-only dashboard pages.
5. **Denial (bring in-house).** Replace n8n; defer richness to ERA phase.

> Rationale: start where persistence already exists and only hardening is needed (eligibility), establish the reusable patterns (run-log + state machines via coding/PA), then build the heavy net-new spine (Workflow OS/claims), and finish with the externally-owned, data-starved denial path.

---

## 5. Per-agent consolidation template (use for every agent)

For each agent, a PR delivers:

- [ ] **Data contract**: explicit list of tables this agent **owns** (writes) vs **reads**; one writer per owned table.
- [ ] **Schema↔code reconciliation**: every column the code reads/writes exists; every owned table has a real, seeded read-path; no schema-only tables left in the agent's scope.
- [ ] **State machine**: documented status transitions + a CHECK constraint; the transition code exists and is tested.
- [ ] **Persistence**: results are durably stored (not just returned over HTTP); an events/audit row is appended.
- [ ] **Security & plane**: RLS enabled with role-aware policies (no `using(true)` on PHI); PHI columns identified for the Neon plane; server-only writes where appropriate.
- [ ] **Forward migration** `045_+` (idempotent, RLS-by-default) per the [migrations README](../supabase/migrations/README.md) conventions.
- [ ] **Tests + fixtures** for the read/write paths and the state machine.
- [ ] **Docs**: update [database-operational-guide.md](database-operational-guide.md) ownership tags (⚪/🟡 → 🟢).

---

*Ratings derived from the code audit summarized in `database-operational-guide.md`. Re-score after each agent's consolidation PR.*
