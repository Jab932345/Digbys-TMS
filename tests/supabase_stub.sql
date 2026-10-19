-- Local stand-in for the Supabase project, used only by the tests.
-- Mirrors the tables the current dashboard reads and Supabase's built-in roles.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end $$;
create schema extensions;
create extension pgcrypto with schema extensions;
create schema storage;
create table storage.buckets (id text primary key, name text, public boolean default false);
create publication supabase_realtime;
alter database tms set search_path = public, extensions;

create table public.units (
  id uuid primary key default extensions.gen_random_uuid(),
  name text not null,
  location text,
  warn_temp numeric not null,
  critical_temp numeric not null,
  active boolean default true,
  created_at timestamptz default now()
);
create table public.temperature_live (
  id bigserial primary key,
  unit_name text not null,
  temp double precision,
  alert text,
  snoozed boolean default false,
  defrost_mode boolean,
  updated_at timestamptz default now()
);
create table public.temperature_logs (
  id bigserial primary key,
  unit_name text,
  temp double precision,
  alert text,
  created_at timestamptz default now()
);
create table public.commands (
  id bigserial primary key,
  unit_name text, command text, payload jsonb, executed boolean default false,
  created_at timestamptz default now()
);
grant usage on schema public, extensions to anon, authenticated, service_role;
grant all on all tables in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;

insert into public.units (name, location, warn_temp, critical_temp) values
  ('Main Freezer', 'Unit', -14, -10),
  ('Main Fridge', 'Unit', 11, 15),
  ('Walk-in Fridge', 'Unit', 8, 10);
insert into public.temperature_live (unit_name, temp, alert, snoozed, defrost_mode)
  values ('Main Fridge', 4.1, 'none', false, false);
