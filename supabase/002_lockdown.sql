-- Run ONLY after all 6 units are on firmware v2 and the website sign-in works.
-- Takes away the public (anon) key's direct access to the TMS tables, so nobody
-- holding the website's public key can silence an alarm or change a threshold.
-- Units keep working: they only call node_sync, which checks each unit's own key.
-- All or nothing; safe to re-run.

begin;

do $$
declare t text;
begin
  foreach t in array array['units', 'temperature_live', 'temperature_logs', 'commands'] loop
    if to_regclass('public.' || t) is not null then
      execute format('revoke insert, update, delete, truncate, select on public.%I from anon', t);
      execute format('alter table public.%I enable row level security', t);
    end if;
  end loop;
end $$;

notify pgrst, 'reload schema';

commit;
