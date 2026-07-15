# Application Postgres migrations (Supabase pilot)

**Scope:** Plain Postgres DDL for the application data plane (`platform`, `patient`,
`rcm`, `agents`, …). Applied to the **Supabase Postgres** project via `DATABASE_URL`.
No browser-facing grants (`anon` / `authenticated` / `service_role`), no Edge
triggers — the browser reaches this data only through the FastAPI BFF.

> Historical note: these files lived under `neon/migrations/` when a dedicated Neon
> PHI plane was planned. The pilot uses Supabase only; the folder was renamed to
> `schema/migrations/`.

## Apply

```bash
# DATABASE_URL = Supabase Postgres (Settings → Database → connection string)
python scripts/apply_schema_migrations.py
```

- `NEON_DATABASE_URL` still works as a **legacy alias** for the same DSN.
- On Supabase hosts the runner skips Neon-only files (`003_voice_verification.sql`,
  `006_pgaudit.sql`) and uses `011_pgaudit_supabase.sql` instead.
- Numbered files include realtime notify (008), `opendental_connections` (009),
  `worker_state` (010), pgaudit_supabase (011), and OD `writeback_full` (012).

## Tenant isolation (pilot)

Application-layer `practice_id` filters + session GUC `app.practice_id`. The
Supabase `postgres` role bypasses RLS — do not treat FORCE RLS as active
protection until a non-superuser app role is introduced.

## Compliance

Supabase Pro has **no BAA**. Suitable for a limited pilot only.
