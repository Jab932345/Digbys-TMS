#!/bin/bash
# Sous vide mode (003): too-cold alarm per unit, off by default, re-runnable.
set -u
cd "$(dirname "$0")/.."
P="psql -h /tmp -p 55432 -U postgres -v ON_ERROR_STOP=1 -q"
fails=0
ok(){ echo "PASS $1"; }
bad(){ echo "FAIL $1"; fails=$((fails+1)); }
q(){ $P -d tms -tAc "$1" 2>&1; }

$P -c "drop database if exists tms" -c "create database tms" >/dev/null 2>&1
$P -d tms -f tests/supabase_stub.sql >/dev/null 2>&1
$P -d tms -f supabase/001_sync_ota.sql >/dev/null 2>&1 || bad "001"
$P -d tms -f supabase/003_sous_vide.sql >/dev/null 2>&1 && ok "003 runs" || bad "003 runs"
$P -d tms -f supabase/003_sous_vide.sql >/dev/null 2>&1 && ok "003 re-runs" || bad "003 re-runs"
q "insert into units(name,warn_temp,critical_temp,device_id,device_key_hash,active) values ('SV',62,64,'sv-1',encode(extensions.digest('k','sha256'),'hex'),true)" >/dev/null
a(){ q "select node_sync('sv-1','k',$1)->>'alert'"; }
[ "$(a 40)" = "ok" ] && ok "mode off: 40 is ok" || bad "mode off 40: $(a 40)"
[ "$(a 63)" = "warning" ] && ok "too hot still warns" || bad "hot warn: $(a 63)"
[ "$(q "select set_low_alarm('SV',58,56)")" = "" ] && ok "turn on" || bad "turn on"
[ "$(a 60)" = "ok" ] && ok "60 in range ok" || bad "60: $(a 60)"
[ "$(a 57.5)" = "warning" ] && ok "57.5 too-cold warning" || bad "57.5: $(a 57.5)"
[ "$(a 56)" = "critical" ] && ok "56 too-cold critical" || bad "56: $(a 56)"
[ "$(a 65)" = "critical" ] && ok "65 too-hot critical" || bad "65: $(a 65)"
[ "$(q "select alert from temperature_live where unit_name='SV'")" = "critical" ] && ok "live row updated" || bad "live row"
q "select set_low_alarm('SV',56,58)" | grep -q "must be below too-cold warning" && ok "rejects crit>=warn" || bad "crit>=warn"
q "select set_low_alarm('SV',63,60)" | grep -q "below the too-hot warning" && ok "rejects overlap with hot" || bad "overlap"
q "select set_low_alarm('SV',58,null)" | grep -q "both" && ok "rejects half set" || bad "half"
q "select set_low_alarm('SV',null,null)" >/dev/null; [ "$(a 40)" = "ok" ] && ok "turn off: 40 ok again" || bad "off: $(a 40)"
q "set role authenticated; select set_low_alarm('SV',58,56)" | grep -qi "denied" && bad "authenticated can't call" || ok "authenticated can call"
$P -d tms -f supabase/temp_close.sql >/dev/null 2>&1 && ok "temp_close runs" || bad "temp_close"
q "set role anon; select set_low_alarm('SV',58,56)" | grep -qi "denied" && ok "anon blocked after temp_close" || bad "anon still allowed"
echo; echo "$fails failure(s)"; exit $fails
