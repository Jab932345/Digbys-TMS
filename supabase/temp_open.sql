-- TEMP: let the website work without signing in. Undo with temp_close.sql.
reset role;
begin;
grant execute on function public.set_snooze(text,integer,text) to anon;
grant execute on function public.set_defrost(text,boolean,integer) to anon;
grant execute on function public.update_unit_settings(text,text,text,numeric,numeric,integer) to anon;
grant execute on function public.set_fw_target(text,text[]) to anon;
grant select on public.units, public.temperature_live, public.temperature_logs, public.firmware_releases to anon;
do $$
declare t text;
begin
  foreach t in array array['units','temperature_live','temperature_logs'] loop
    if not exists (select 1 from pg_policies where tablename = t and policyname = 'tms_anon_read_temp') then
      execute format('create policy tms_anon_read_temp on public.%I for select to anon using (true)', t);
    end if;
  end loop;
end $$;
notify pgrst, 'reload schema';
commit;
