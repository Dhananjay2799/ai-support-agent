-- Recreates the pieces Supabase provides out of the box, so the migrations in
-- supabase/migrations run unchanged against a plain local Postgres (tests, CI).
create schema if not exists extensions;
-- Supabase puts `extensions` on the search path, so operators like `<=>` resolve unqualified.
do $$
begin
  execute format('alter database %I set search_path = "$user", public, extensions', current_database());
end $$;
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
