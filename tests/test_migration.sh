#!/bin/bash
# Migration safety: duplicate live rows are cleaned up; duplicate unit names
# abort the whole migration with nothing changed.
set -u
cd "$(dirname "$0")/.."
P="psql -h /tmp -p 55432 -U postgres -v ON_ERROR_STOP=1 -q"
fails=0
ok(){ echo "PASS $1"; }
bad(){ echo "FAIL $1"; fails=$((fails+1)); }

fresh(){ $P -c "drop database if exists tms" -c "create database tms" >/dev/null 2>&1 && $P -d tms -f tests/supabase_stub.sql >/dev/null 2>&1; }

fresh
$P -d tms -c "insert into temperature_live(unit_name,temp,alert,updated_at) values ('Main Fridge',9.9,'none',now()-interval '1 day')" >/dev/null
if $P -d tms -f supabase/001_sync_ota.sql >/dev/null 2>&1; then ok "migration runs with duplicate live rows"; else bad "migration with duplicate live rows"; fi
n=$($P -d tms -tAc "select count(*)||':'||max(temp) from temperature_live where unit_name='Main Fridge'")
[ "$n" = "1:4.1" ] && ok "kept only the newest live row ($n)" || bad "dedupe kept wrong row ($n)"

fresh
$P -d tms -c "insert into units(name,warn_temp,critical_temp) values ('Main Fridge',1,2)" >/dev/null
if $P -d tms -f supabase/001_sync_ota.sql >/tmp/mig.err 2>&1; then bad "duplicate unit names should abort"; else
  grep -q "Two units share a name" /tmp/mig.err && ok "duplicate unit names abort with a clear message" || bad "wrong error: $(tail -1 /tmp/mig.err)"
fi
c=$($P -d tms -tAc "select count(*) from information_schema.columns where table_name='units' and column_name='device_id'")
f=$($P -d tms -tAc "select count(*) from pg_proc where proname='node_sync'")
[ "$c$f" = "00" ] && ok "aborted migration changed nothing" || bad "partial migration left behind (cols $c funcs $f)"

fresh
$P -d tms -f supabase/001_sync_ota.sql >/dev/null 2>&1
if $P -d tms -f supabase/002_lockdown.sql >/dev/null 2>&1; then ok "lockdown runs"; else bad "lockdown failed"; fi
if $P -d tms -c "set role anon" -c "update units set warn_temp=99" >/dev/null 2>&1; then bad "anon can still write units"; else ok "anon cannot write units after lockdown"; fi
if $P -d tms -c "set role anon" -c "select node_sync('x','y',1)" 2>&1 | grep -q "unauthorised device"; then ok "units can still call node_sync after lockdown"; else bad "node_sync blocked by lockdown"; fi
echo; echo "$fails failure(s)"; exit $fails
