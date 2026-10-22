-- Sous vide mode: a "too cold" alarm, switchable per unit from the website.
-- Off (both null) = the unit only alarms when too hot, as before.
-- On = warning at or below low_warn_temp, critical at or below low_crit_temp.
-- No firmware change needed: the server decides the alert level, and the
-- website sounds the alarm for too-cold. Safe to re-run.
reset role;
begin;

alter table public.units add column if not exists low_warn_temp numeric;
alter table public.units add column if not exists low_crit_temp numeric;

create or replace function public.tms_alert(p_temp numeric, p_warn numeric, p_crit numeric,
                                            p_lwarn numeric, p_lcrit numeric)
returns text language sql immutable as $$
  select case
    when p_temp is null                          then 'critical'   -- probe fault
    when p_temp >= p_crit                        then 'critical'
    when p_lcrit is not null and p_temp <= p_lcrit then 'critical'
    when p_temp >= p_warn                        then 'warning'
    when p_lwarn is not null and p_temp <= p_lwarn then 'warning'
    else 'ok' end
$$;

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

  v_alert := public.tms_alert(p_temp, u.warn_temp, u.critical_temp, u.low_warn_temp, u.low_crit_temp);

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

-- Turn sous vide mode on (both numbers) or off (both null) for one unit.
create or replace function public.set_low_alarm(p_unit text, p_low_warn numeric, p_low_crit numeric)
returns void
language plpgsql security definer set search_path = public as $$
declare u public.units%rowtype;
begin
  select * into u from public.units where name = p_unit for update;
  if not found then raise exception 'unit % not found', p_unit; end if;
  if (p_low_warn is null) <> (p_low_crit is null) then
    raise exception 'set both too-cold numbers, or neither';
  end if;
  if p_low_warn is not null then
    if p_low_crit >= p_low_warn then
      raise exception 'too-cold critical must be below too-cold warning';
    end if;
    if p_low_warn >= u.warn_temp then
      raise exception 'too-cold warning must be below the too-hot warning (%)', u.warn_temp;
    end if;
  end if;
  update public.units set low_warn_temp = p_low_warn, low_crit_temp = p_low_crit where name = p_unit;
end $$;

revoke all on function public.set_low_alarm(text,numeric,numeric) from public;
grant execute on function public.set_low_alarm(text,numeric,numeric) to authenticated;
-- TEMP while the website has no sign-in (temp_close.sql takes this away again):
grant execute on function public.set_low_alarm(text,numeric,numeric) to anon;

notify pgrst, 'reload schema';
commit;
