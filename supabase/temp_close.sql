-- Turns the sign-in back on at the database (run after setting OPEN_MODE=false).
reset role;
begin;
revoke execute on function public.set_snooze(text,integer,text) from anon;
revoke execute on function public.set_defrost(text,boolean,integer) from anon;
revoke execute on function public.update_unit_settings(text,text,text,numeric,numeric,integer) from anon;
revoke execute on function public.set_fw_target(text,text[]) from anon;
do $$ begin
  if to_regprocedure('public.set_low_alarm(text,numeric,numeric)') is not null then
    revoke execute on function public.set_low_alarm(text,numeric,numeric) from anon;
  end if;
end $$;
do $$
declare t text;
begin
  foreach t in array array['units','temperature_live','temperature_logs'] loop
    execute format('drop policy if exists tms_anon_read_temp on public.%I', t);
  end loop;
end $$;
notify pgrst, 'reload schema';
commit;
