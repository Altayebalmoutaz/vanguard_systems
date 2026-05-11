# [ARCHIVED] 10-Week Production Pilot Roadmap: Medical RCM AI

> **This document is archived and no longer reflects the platform direction.**
>
> - **Active roadmap:** see [`docs/production-roadmap.md`](../production-roadmap.md).
> - **Active orchestration:** linear, tool-first "Claw-style" agents in
>   [`app/agents/`](../../app/agents/) plus the layered eligibility pipeline in
>   [`app/eligibility/`](../../app/eligibility/). **LangGraph was evaluated and
>   not adopted** — the deterministic linear pattern proved sufficient for the
>   current RCM workflows and is easier to audit for HIPAA.
> - **Active observability:** structured Python logging + Supabase audit tables
>   (`audit_events`, `eligibility_audit_log`). **Opik was not adopted.**
> - **Active embeddings:** Jina v3 via [`app/services/cdt_vector_memory.py`](../../app/services/cdt_vector_memory.py)
>   for CDT semantic search, but as an in-database / in-process helper rather
>   than a LangGraph RAG node.
> - **Active LLM provider:** **OpenRouter** for prior-auth / denial / coding LLM
>   prompts, with PHI scrubbing in [`app/security/phi.py`](../../app/security/phi.py)
>   before any payload leaves the trust boundary. A dedicated BAA-covered
>   provider is required before production PHI traffic — see
>   [`SECURITY.md`](../../SECURITY.md).
>
> This file is preserved for historical context only. Do not derive new work
> from it; consult `docs/production-roadmap.md` instead.

---

## Original document (preserved verbatim below)

This document provides a highly detailed, tactical roadmap to move the current FastAPI + Supabase prototype into a **production-ready, HIPAA-compliant pilot** within 10 weeks. This plan integrates LangGraph, Jina, Opik, and other advanced tooling while bypassing premature infrastructure complexity (like OpenTofu) until post-pilot. 

## Phase 1: Baseline Ops & Quality Assurance (Weeks 1-2)

Goal: Harden the current code to prevent regressions when porting workflows to LangGraph.

- **Linting & Typing:** Implement `ruff` and `mypy` aggressively. Configure GitHub Actions to block PRs failing these checks.
- **Error Tracking:** Install `sentry-sdk` into the FastAPI backend. Set up capturing for unhandled exceptions.
- **Database Prep:** Enable `pgvector` inside the existing Supabase instance via dashboard or SQL migration. No need to migrate off Supabase.
- **Traceability Setup:** Integrate the `opik` SDK. You must instrument all current LLM calls (via OpenRouter) so we have a baseline for evaluation before LangGraph takes over.

## Phase 2: The LangGraph Orchestration Layer (Weeks 3-5)

Goal: Move from rigid functional pipelines to fault-tolerant, stateful AI graphs.

- **State Model Definition:** Define strict Pydantic models for the overall RCM state (e.g., `ClaimState`, `AuthStatus`, etc.) that pass between graph nodes.
- **Node Implementation:** Convert the 5 existing discrete workflows into LangGraph Nodes:
  1. Clinical Coding (uses Grok/OpenRouter)
  2. Prior Auth Evaluation
  3. Claim Draft Generation
  4. Denial Intelligence
- **Human-in-the-Loop:** Leverage LangGraph's `interrupt_before` and `interrupt_after` hooks to formally pause execution for the Biller Review UI.
- **Document RAG:** Integrate the `Jina` Reader API for ingesting complex PDFs (like patient charts or EOBs), embedding them via `Jina` embeddings, and storing them in Supabase `pgvector`.

## Phase 3: The Data Foundation (Weeks 6-7)

To hit a viable pilot, you must move beyond mocked strings into structured datasets.

### Required Datasets
- **Terminology:** Absolute latest CDT (dental codes) and ICD-10 (diagnosis codes) subsets. You must acquire licenses if using CDT commercially.
- **Payer Guidelines:** Scrape or acquire PDF rules specifically for your chosen pilot payer (e.g., Delta Dental).
- **Synthetic Patient Data (MIND THE PHI):** Generate or acquire 50-100 realistic but mathematically synthesized clinical notes and claim profiles to run automated regression tests via Opik.
- **Mock ERAs:** Realistic 835 format denial files mapped to specific test cases (e.g., CO-50 missing requirement).

## Phase 4: Production Auditability & Auditing (Week 8)

Goal: If an auditor or a Chief Medical Officer asks "Why did the AI submit this claim?", you must have the answer.

- **Immutable Audit Log Table:** Create an `audit_events` table in Supabase.
  - Required columns: `event_id`, `claim_id`, `user_id` (human or agent), `action_taken`, `old_state`, `new_state`, `timestamp`, `opik_trace_id`.
- **Trigger-based Auditing:** Add Postgres triggers on the Supabase claim tables to ensure changes are logged even if the FastAPI backend is bypassed.
- **Agent Reasoning Links:** Every claim draft generated must link back to its specific Opik trace ID in the database so billers can "click to see AI reasoning".

## Phase 5: HIPAA Compliance & Security Baseline (Weeks 9-10)

Goal: Lock down the environment strictly for live PHI (Protected Health Information).

### The HIPAA "Must-Haves"
1. **Execute BAAs (Business Associate Agreements):**
   - You MUST sign a BAA with Supabase.
   - You MUST sign a BAA with your hosting provider (e.g., Render/AWS).
   - You MUST sign a BAA with your LLM provider (e.g., Anthropic/OpenAI/Grok). *Note: OpenRouter does not currently offer strict HIPAA BAAs, so you must route directly to the core provider APIs if handling actual PHI.*
2. **Encryption:**
   - Data in transit: Enforce TLS/HTTPS everywhere.
   - Data at rest: Supabase encrypts at rest by default, but confirm your specific configurations.
3. **Role-Based Access Control (RBAC):**
   - Configure Supabase Row Level Security (RLS) policies so a biller from Practice A cannot query rows belonging to Practice B.
4. **Session Management:** Enforce short-lived JWT tokens (e.g., 30-min expiry) for the frontend UI with automatic logouts upon inactivity.

## Pilot Launch Post-Condition

By the end of Week 10, the platform will be deployed securely on a managed PaaS, orchestrated by LangGraph, evaluated continuously by Opik, and legally protected by zero-retention BAAs. 
