-- Wave 7: enable pgaudit on HIPAA Neon projects (preloaded on Neon HIPAA-enabled branches).
-- Run manually or via migration after BAA/HIPAA is enabled in Neon console.

begin;

create extension if not exists pgaudit;

-- Log DDL + role changes + writes on PHI schemas (adjust in production as needed).
alter role neondb_owner set pgaudit.log to 'ddl, role, write';
alter role neondb_owner set pgaudit.log_catalog to 'off';
alter role neondb_owner set pgaudit.log_parameter to 'on';
alter role neondb_owner set pgaudit.log_relation to 'on';

comment on extension pgaudit is 'Wave 7: audit PHI-plane writes at Postgres level';

commit;
