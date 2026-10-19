-- Digby's TMS v2: two-way sync (snooze/defrost) and over-the-air firmware.
-- Run once in the Supabase SQL editor of project jaajrllkozknilvmdezt.
-- All or nothing: if any line fails, nothing is changed. Safe to re-run.

begin;

create extension if not exists pgcrypto with schema extensions;

-- ── Units: device identity, alarm behaviour, firmware target ──────────────
alter table public.units
  add column if not exists device_id        text,
  add column if not exists device_key_hash  text,
  add column if not exists alarm_delay_s    integer not null default 180,
  add column if not exists sync_interval_s  integer not null default 5,
  add column if not exists log_interval_s   integer not null default 60,
  add column if not exists fw_target        text;

do $$ begin
  if exists (select name from public.units group by name having count(*) > 1) then
    raise exception 'Two units share a name - rename one on the website, then run this again';
  end if;
end $$;
create unique index if not exists units_device_id_uq on public.units(device_id);
create unique index if not exists units_name_uq on public.units(name);

-- ── Live row: the single shared state both the app and the unit obey ───────
alter table public.temperature_live
  add column if not exists snooze_until   timestamptz,
  add column if not exists snooze_source  text,
  add column if not exists defrost_mode   boolean not null default false,
  add column if not exists defrost_until  timestamptz,
  add column if not exists fw_version     text,
  add column if not exists fw_status      text,
  add column if not exists rssi           integer,
  add column if not exists last_seen      timestamptz,
  add column if not exists siren          boolean not null default false,
  add column if not exists state_rev      bigint  not null default 0;

-- Keep only the newest live row per unit (old code could leave duplicates).
delete from public.temperature_live a
 using public.temperature_live b
 where a.unit_name = b.unit_name
   and (a.updated_at, a.ctid) < (b.updated_at, b.ctid);
create unique index if not exists temperature_live_unit_name_uq on public.temperature_live(unit_name);

alter table public.temperature_logs
  add column if not exists snoozed boolean not null default false;

create index if not exists temperature_logs_unit_created_idx
  on public.temperature_logs(unit_name, created_at desc);

-- ── Firmware releases (files live in Storage bucket "firmware") ────────────
create table if not exists public.firmware_releases (
  version    text primary key,
  notes      text,
  files      jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.firmware_releases enable row level security;
grant select on public.firmware_releases to authenticated;
do $$ begin
  if not exists (select 1 from pg_policies where tablename='firmware_releases' and policyname='fw_read') then
    create policy fw_read on public.firmware_releases for select to anon, authenticated using (true);
  end if;
end $$;

insert into storage.buckets (id, name, public)
values ('firmware', 'firmware', true)
on conflict (id) do update set public = true;

-- ── Keep the live row attached when a unit is renamed in the app ───────────
create or replace function public.tms_units_rename() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.name is distinct from old.name then
    update public.temperature_live set unit_name = new.name where unit_name = old.name;
  end if;
  return new;
end $$;

drop trigger if exists tms_units_rename on public.units;
create trigger tms_units_rename after update of name on public.units
  for each row execute function public.tms_units_rename();

-- ── Shared helper: alert level from a reading (cold storage: higher = worse) ─
create or replace function public.tms_alert(p_temp numeric, p_warn numeric, p_crit numeric)
returns text language sql immutable as $$
  select case
    when p_temp is null      then 'critical'   -- probe fault is treated as critical
    when p_temp >= p_crit    then 'critical'
    when p_temp >= p_warn    then 'warning'
    else 'ok' end
$$;

-- ── node_sync: the ONLY call a unit makes ──────────────────────────────────
-- Every few seconds the unit reports its reading (and any button press) and
-- gets back the authoritative state: snooze time left, defrost, thresholds,
-- firmware target. Server time decides every expiry, so no clock drift.
--   p_snooze_s : null = no button action; 0 = clear; >0 = snooze for that many seconds
--   p_siren    : whether the siren is sounding right now (shown on the dashboard)
create or replace function public.node_sync(
  p_device   text,
  p_key      text,
  p_temp     numeric,
  p_fw       text    default null,
  p_rssi     integer default null,
  p_snooze_s integer default null,
  p_siren    boolean default false,
  p_fw_status text   default null
) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare
  u        public.units%rowtype;
  lv       public.temperature_live%rowtype;
  v_now    timestamptz := now();
  v_alert  text;
  v_snz    boolean;
  v_def    boolean;
  v_last   timestamptz;
  v_change boolean;
  v_rev0   bigint;
begin
  select * into u from public.units where device_id = p_device and coalesce(active, true);
  if not found or u.device_key_hash is null
     or u.device_key_hash <> encode(digest(coalesce(p_key, ''), 'sha256'), 'hex') then
    raise exception 'unauthorised device' using errcode = '28000';
  end if;

  if p_temp is not null and (p_temp < -60 or p_temp > 130) then
    p_temp := null;  -- impossible reading = probe fault
  end if;

  v_alert := public.tms_alert(p_temp, u.warn_temp, u.critical_temp);

  insert into public.temperature_live (unit_name, temp, alert, updated_at)
  values (u.name, p_temp, v_alert, v_now)
  on conflict (unit_name) do nothing;

  select * into lv from public.temperature_live where unit_name = u.name for update;
  v_rev0 := lv.state_rev;

  -- Button press on the unit: newest action wins, same as a tap in the app.
  if p_snooze_s is not null then
    lv.snooze_until  := case when p_snooze_s > 0
                             then v_now + make_interval(secs => least(p_snooze_s, 28800))
                             else null end;
    lv.snooze_source := 'unit';
    lv.state_rev     := lv.state_rev + 1;
  end if;

  -- Expire anything past its time.
  if lv.snooze_until is not null and lv.snooze_until <= v_now then
    lv.snooze_until := null;
  end if;
  if lv.defrost_mode and lv.defrost_until is not null and lv.defrost_until <= v_now then
    lv.defrost_mode  := false;
    lv.defrost_until := null;
    lv.state_rev     := lv.state_rev + 1;
  end if;

  v_snz   := lv.snooze_until is not null;
  v_def   := coalesce(lv.defrost_mode, false);

  -- Only write the live row when something visible changed, or every 30 s as a
  -- heartbeat. Every write is pushed to open dashboards, so this keeps realtime
  -- traffic low without slowing anything that matters.
  v_change := lv.last_seen is null
    or v_now - lv.last_seen >= interval '30 seconds'
    or (p_temp is null) <> (lv.temp is null)
    or abs(coalesce(p_temp, 0) - coalesce(lv.temp, 0)) >= 0.2
    or v_alert is distinct from lv.alert
    or p_snooze_s is not null
    or lv.state_rev <> v_rev0
    or v_snz <> coalesce(lv.snoozed, false)
    or v_def is distinct from lv.defrost_mode
    or coalesce(p_siren, false) <> coalesce(lv.siren, false)
    or p_fw is distinct from lv.fw_version
    or p_fw_status is not null;

  if v_change then
  update public.temperature_live set
    temp          = p_temp,
    alert         = v_alert,
    snoozed       = v_snz,
    snooze_until  = lv.snooze_until,
    snooze_source = lv.snooze_source,
    defrost_mode  = v_def,
    defrost_until = lv.defrost_until,
    fw_version    = coalesce(p_fw, lv.fw_version),
    fw_status     = coalesce(p_fw_status, lv.fw_status),
    rssi          = p_rssi,
    siren         = coalesce(p_siren, false),
    last_seen     = v_now,
    updated_at    = v_now,
    state_rev     = lv.state_rev
  where unit_name = u.name;
  end if;

  -- HACCP log: every log_interval_s, plus immediately on any change of alert level.
  select max(created_at) into v_last from public.temperature_logs where unit_name = u.name;
  if v_last is null
     or v_now - v_last >= make_interval(secs => greatest(u.log_interval_s, 10))
     or v_alert is distinct from lv.alert then
    insert into public.temperature_logs (unit_name, temp, alert, snoozed, created_at)
    values (u.name, p_temp, v_alert, v_snz, v_now);
  end if;

  return jsonb_build_object(
    'name',       u.name,
    'snooze_s',   case when v_snz then ceil(extract(epoch from (lv.snooze_until - v_now)))::int else 0 end,
    'defrost',    v_def,
    'defrost_s',  case when v_def and lv.defrost_until is not null
                       then ceil(extract(epoch from (lv.defrost_until - v_now)))::int else 0 end,
    'warn',       u.warn_temp,
    'crit',       u.critical_temp,
    'delay_s',    u.alarm_delay_s,
    'interval_s', greatest(u.sync_interval_s, 2),
    'alert',      v_alert,
    'fw_target',  u.fw_target,
    'rev',        lv.state_rev
  );
end $$;

-- ── App-side controls (signed-in users only) ───────────────────────────────
-- p_mode 'add' extends a running snooze (the app's preset buttons); 'set' replaces it.
create or replace function public.set_snooze(p_unit text, p_minutes integer, p_mode text default 'add')
returns timestamptz
language plpgsql security definer set search_path = public as $$
declare v_now timestamptz := now(); v_cur timestamptz; v_new timestamptz;
begin
  if p_minutes is null or p_minutes < 0 or p_minutes > 480 then
    raise exception 'snooze must be 0-480 minutes';
  end if;
  select snooze_until into v_cur from public.temperature_live where unit_name = p_unit for update;
  if not found then raise exception 'unit % has not reported yet', p_unit; end if;
  if p_minutes = 0 then
    v_new := null;
  elsif p_mode = 'add' and v_cur is not null and v_cur > v_now then
    v_new := least(v_cur + make_interval(mins => p_minutes), v_now + interval '8 hours');
  else
    v_new := v_now + make_interval(mins => p_minutes);
  end if;
  update public.temperature_live
     set snooze_until = v_new, snoozed = v_new is not null,
         snooze_source = 'app', state_rev = state_rev + 1, updated_at = v_now
   where unit_name = p_unit;
  return v_new;
end $$;

create or replace function public.set_defrost(p_unit text, p_on boolean, p_minutes integer default 120)
returns timestamptz
language plpgsql security definer set search_path = public as $$
declare v_until timestamptz;
begin
  v_until := case when p_on then now() + make_interval(mins => least(greatest(coalesce(p_minutes,120), 5), 480)) end;
  update public.temperature_live
     set defrost_mode = p_on, defrost_until = v_until,
         state_rev = state_rev + 1, updated_at = now()
   where unit_name = p_unit;
  if not found then raise exception 'unit % has not reported yet', p_unit; end if;
  return v_until;
end $$;

create or replace function public.update_unit_settings(
  p_unit text, p_name text, p_location text, p_warn numeric, p_crit numeric, p_delay_s integer default null)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if p_warn is null or p_crit is null or p_warn >= p_crit then
    raise exception 'warning must be below critical';
  end if;
  update public.units
     set name = p_name, location = p_location, warn_temp = p_warn, critical_temp = p_crit,
         alarm_delay_s = coalesce(p_delay_s, alarm_delay_s)
   where name = p_unit;
  if not found then raise exception 'unit not found'; end if;
end $$;

-- p_units null = every active unit
create or replace function public.set_fw_target(p_version text, p_units text[] default null)
returns integer
language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  if p_version is not null and not exists (select 1 from public.firmware_releases where version = p_version) then
    raise exception 'unknown firmware version %', p_version;
  end if;
  update public.units set fw_target = p_version
   where coalesce(active, true) and (p_units is null or name = any(p_units));
  get diagnostics n = row_count;
  return n;
end $$;

-- ── Permissions ────────────────────────────────────────────────────────────
revoke all on function public.node_sync(text,text,numeric,text,integer,integer,boolean,text) from public;
revoke all on function public.set_snooze(text,integer,text) from public;
revoke all on function public.set_defrost(text,boolean,integer) from public;
revoke all on function public.update_unit_settings(text,text,text,numeric,numeric,integer) from public;
revoke all on function public.set_fw_target(text,text[]) from public;
revoke all on function public.tms_units_rename() from public;

grant execute on function public.node_sync(text,text,numeric,text,integer,integer,boolean,text) to anon, authenticated;
grant execute on function public.set_snooze(text,integer,text) to authenticated;
grant execute on function public.set_defrost(text,boolean,integer) to authenticated;
grant execute on function public.update_unit_settings(text,text,text,numeric,numeric,integer) to authenticated;
grant execute on function public.set_fw_target(text,text[]) to authenticated;

-- Signed-in dashboard can read everything it shows (no effect if RLS is off).
do $$
declare t text;
begin
  foreach t in array array['units','temperature_live','temperature_logs'] loop
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'tms_auth_read') then
      execute format('create policy tms_auth_read on public.%I for select to authenticated using (true)', t);
    end if;
  end loop;
end $$;

-- Realtime must broadcast the live table.
do $$ begin
  if not exists (select 1 from pg_publication_tables
                 where pubname = 'supabase_realtime' and tablename = 'temperature_live') then
    alter publication supabase_realtime add table public.temperature_live;
  end if;
end $$;

notify pgrst, 'reload schema';

commit;
