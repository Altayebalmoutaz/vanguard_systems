# Security policy — Vanguard MD

> Vanguard MD is a HIPAA-relevant Revenue Cycle Management system handling Protected
> Health Information (PHI) for dental and medical billing. Treat any access to this
> repository, the production Supabase project, or the running services as access to
> regulated data.

## Reporting a vulnerability

- **Email:** security@vanguard-md.example   <!-- update before going public -->
- **Encrypted reports:** request our PGP key via the address above.
- Please **do not** open public GitHub issues for security findings.

We aim to acknowledge within **2 business days** and ship a fix or mitigation within
**30 days** for high-severity issues. Coordinated disclosure is appreciated.

## HIPAA posture (current)

This is a working summary; the authoritative documents are the BAAs and risk
assessment held by the compliance owner.

| Area                         | Status        | Notes |
|------------------------------|---------------|-------|
| Business Associate Agreements | partial       | Confirm BAAs are in place with: Supabase, Stedi, OpenRouter (and underlying providers), Jina, Vyne (if/when integrated). Treat any vendor without a BAA as **not approved for PHI**. |
| PHI scrubbing                | mostly done   | Centralized in `app/security/phi.py` (Presidio + regex). Applied before every outbound LLM call (`app/llm/*`). Roadmap item: extend to Sentry/log forwarders (see audit P5). |
| Encryption at rest           | provider-managed | Supabase Postgres at rest encryption is on by default; verify the project's region and key management. |
| Encryption in transit        | required      | All FastAPI routes must be served over TLS in production. The reverse proxy (Nginx, Traefik, ALB, etc.) is responsible for HSTS, modern ciphers, and OCSP stapling. |
| Authentication               | mostly done   | Centralised dependency at `app/api/auth.py` accepts a Supabase JWT *or* a static `X-API-Key`. Active when `REQUIRE_AUTH=1`; production deployments **must** flip this on. The eligibility sub-app retains its own bearer token. |
| Authorisation / RLS          | mostly done   | Migration `037_eligibility_rls_hardening.sql` enables RLS on `rcm.eligibility_checks` / `rcm.procedure_estimates`, replaces the `using(true)` policies on `rcm.eligibility_requests` with `created_by`-scoped policies, and revokes anon SELECT of `raw_response`. Cross-tenant scoping is the next milestone. |
| Audit logging                | partial       | The eligibility subsystem writes scrubbed audit rows. Other agents do not yet emit structured audit events; Sentry / log aggregation is recommended. |
| Dependency hygiene           | gated by CI   | `ruff` + `pytest` run on every PR. The `supply-chain` job in `.github/workflows/ci.yml` runs `pip-audit` against the resolved Python closure, generates a CycloneDX SBOM with `anchore/sbom-action` (Syft), and runs `osv-scanner` over the merged SBOM (Python + npm). The SBOM is published as a 90-day workflow artifact for vendor / auditor handoff. |

## Secret-rotation policy

| Secret                          | Owner location                  | Rotation cadence | Trigger for ad-hoc rotation |
|---------------------------------|----------------------------------|------------------|------------------------------|
| `SUPABASE_SERVICE_ROLE_KEY`     | Supabase project → Project API   | every 90 days    | suspected leak, contributor offboarding |
| `SUPABASE_ANON_KEY`             | Supabase project → Project API   | every 180 days   | ditto |
| `OPENROUTER_API_KEY`            | OpenRouter dashboard             | every 90 days    | model account changes |
| `STEDI_API_KEY`                 | Stedi console                    | every 90 days    | ditto |
| `JINA_API_KEY`                  | Jina account                     | every 180 days   | ditto |
| `ELIGIBILITY_AGENT_API_KEY`     | Self-issued, stored in Supabase  | every 60 days    | every contributor offboarding |
| `WEBHOOK_SECRET` / `eligibility_dashboard_edge_function_signing_secret` | Supabase Edge config + Vault | every 90 days | suspected leak |
| Supabase database password      | Supabase dashboard               | every 180 days   | suspected leak |
| Edge function HMAC secret       | Supabase Edge config             | every 90 days    | suspected leak |

### When rotating

1. Generate the new credential **before** revoking the old one.
2. Update `.env` for every running deployment (production, staging, ephemeral).
3. Update GitHub Actions secrets and any CI/CD vault.
4. Revoke the old credential.
5. Confirm health checks pass on every environment.
6. Record the rotation in your operations log.

If a secret is suspected to have been committed to Git, treat the **commit hash** as
also-leaked: rotate, force-purge from history (`git filter-repo`), and re-deploy.

### Audit-driven rotation — April 2026

The 2026-04 codebase audit found that `.env` had been embedded in a transcript and
must be treated as compromised. The local checkout has been reset:

- `.env` now contains only placeholder names and the rotation runbook header.
- The pre-rotation values were saved (gitignored) at `.env.OLD.local` for
  reference; **do not** copy them back without rotating upstream first.

Rotation runbook to clear the audit finding:

1. **Supabase** — `Project Settings → API`. Click *Reset JWT secret*; this
   cycles the service-role and anon JWTs in one step. Copy the new keys plus
   the `JWT Settings → JWT Secret` value into the new `.env`.
2. **Stedi** — `Dashboard → API Keys`. Revoke the existing key, create a new
   one, paste it into `STEDI_API_KEY`.
3. **OpenRouter** — <https://openrouter.ai/keys>. Delete and reissue.
4. **Jina** — Account → API keys. Delete and reissue.
5. Re-deploy every running environment so the new values are picked up; delete
   `.env.OLD.local` after verifying production health.

Until step 5 completes, treat the local install as **not approved for PHI**.

## .env discipline

- The committed file is **`.env.example`** only. The runtime `.env` is gitignored.
- The pre-commit hook configured in `.gitignore` blocks `.env` from being staged. Do not
  bypass it with `-f`.
- Any commit that introduces a real Supabase project URL, JWT, or OpenRouter / Stedi /
  Jina key triggers a **mandatory rotation** of that key, even if the commit was reverted.

## Threat model (one-paragraph summary)

The two highest-impact attacker goals are (1) extracting PHI from the Supabase
`eligibility_*` tables (member IDs, subscriber IDs, full 271 payloads in
`raw_response`) and (2) tricking the agent layer into exfiltrating PHI to an
OpenRouter LLM call. Mitigations: enforce Supabase RLS service-role-only on PHI
tables, scrub PHI before any outbound LLM call (`app/security/phi.py`), never
log raw 270/271 payloads, require authentication on every FastAPI route, and
treat the `eligibility_dashboard/` Next.js app as privileged (Supabase JWT only,
no anon).
