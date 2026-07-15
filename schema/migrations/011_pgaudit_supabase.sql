-- 011_pgaudit_supabase.sql
-- Session/audit logging for the Supabase-only pilot (analog of 006_pgaudit.sql,
-- which targeted Neon). pgaudit ships with Supabase; the extension can be created
-- here, but role-level settings need a privileged role, so the block is guarded:
-- it logs a notice instead of failing when permissions are missing.
--
-- Review queries: Supabase Dashboard -> Logs -> Postgres, filter "AUDIT:".

do $$
begin
  begin
    create extension if not exists pgaudit;
  exception when insufficient_privilege or undefined_file then
    raise notice 'pgaudit extension unavailable/not permitted; enable it from the Supabase dashboard (Database -> Extensions)';
  end;

  begin
    -- Log DDL and role changes plus writes; reads on PHI tables are covered by
    -- app-level audit (audit.audit_logs / logs.eligibility_audit_log).
    execute format('alter role %I set pgaudit.log = ''ddl, role, write''', current_user);
    execute format('alter role %I set pgaudit.log_relation = on', current_user);
  exception when insufficient_privilege then
    raise notice 'pgaudit role settings not permitted; set pgaudit.log via the Supabase dashboard SQL editor as the postgres role';
  end;
end
$$;
