"""Database tests: run against a local Postgres loaded with supabase_stub.sql + 001."""
import hashlib
import json
import sys
import time

import psycopg2

DSN = 'host=/tmp port=55432 user=postgres dbname=tms'
fails = 0


def check(cond, what):
    global fails
    print(('PASS ' if cond else 'FAIL ') + what)
    if not cond:
        fails += 1


def conn(role=None):
    c = psycopg2.connect(DSN)
    c.autocommit = True
    if role:
        c.cursor().execute('set role ' + role)
    return c


su = conn()
cur = su.cursor()
KEY = 'test-key-123'
cur.execute("update units set device_id='tms-02', device_key_hash=%s, alarm_delay_s=10 where name='Main Fridge'",
            (hashlib.sha256(KEY.encode()).hexdigest(),))
cur.execute("update units set device_id='tms-01', device_key_hash=%s where name='Main Freezer'",
            (hashlib.sha256(b'other').hexdigest(),))

anon = conn('anon').cursor()
auth = conn('authenticated').cursor()


def sync(**kw):
    a = dict(p_device='tms-02', p_key=KEY, p_temp=4.0, p_fw='2.0.0', p_rssi=-60,
             p_snooze_s=None, p_siren=False, p_fw_status=None)
    a.update(kw)
    anon.execute('select node_sync(%(p_device)s,%(p_key)s,%(p_temp)s,%(p_fw)s,%(p_rssi)s,%(p_snooze_s)s,%(p_siren)s,%(p_fw_status)s)', a)
    return anon.fetchone()[0]


def live(name='Main Fridge'):
    cur.execute('select temp,alert,snoozed,snooze_until,snooze_source,defrost_mode,defrost_until,siren,fw_version,last_seen,state_rev '
                'from temperature_live where unit_name=%s', (name,))
    return cur.fetchone()


def nlogs(name='Main Fridge'):
    cur.execute('select count(*) from temperature_logs where unit_name=%s', (name,))
    return cur.fetchone()[0]


# 1. Auth
for bad in [dict(p_key='wrong'), dict(p_device='nope'), dict(p_device='tms-01', p_key=KEY)]:
    try:
        sync(**bad)
        check(False, 'rejects bad device/key %s' % bad)
    except psycopg2.Error as e:
        check(e.pgcode == '28000', 'rejects bad device/key %s (%s)' % (bad, e.pgcode))
        anon.connection.rollback() if not anon.connection.autocommit else None

# 2. Normal reading
r = sync(p_temp=4.0)
check(r['alert'] == 'ok' and r['snooze_s'] == 0 and r['defrost'] is False, 'normal reading -> ok, no snooze')
check(float(r['warn']) == 11 and float(r['crit']) == 15 and r['delay_s'] == 10, 'thresholds returned')
check(r['interval_s'] == 5 and r['fw_target'] is None and r['name'] == 'Main Fridge', 'interval/name/fw_target')
lv = live()
check(lv[0] == 4.0 and lv[1] == 'ok' and lv[8] == '2.0.0' and lv[9] is not None, 'live row updated')
n0 = nlogs()
check(n0 == 1, 'first reading logged')
sync(p_temp=4.1)
check(nlogs() == n0, 'second reading within interval not logged')

# 3. Alert levels and immediate log on change
r = sync(p_temp=12.0)
check(r['alert'] == 'warning' and nlogs() == n0 + 1, 'warning logged immediately')
r = sync(p_temp=15.0)
check(r['alert'] == 'critical' and nlogs() == n0 + 2, 'critical at exactly crit, logged')
r = sync(p_temp=None)
check(r['alert'] == 'critical', 'probe fault = critical')
r = sync(p_temp=200)
check(r['alert'] == 'critical' and live()[0] is None, 'impossible reading stored as fault')

# 4. Snooze from the unit
rev0 = live()[10]
r = sync(p_temp=16, p_snooze_s=1800)
lv = live()
check(1795 <= r['snooze_s'] <= 1800 and lv[2] is True and lv[4] == 'unit', 'unit snooze stored (%s s)' % r['snooze_s'])
check(lv[10] == rev0 + 1, 'state_rev bumped')
r = sync(p_temp=16)
check(1790 <= r['snooze_s'] <= 1800, 'snooze persists on next sync')
r = sync(p_temp=16, p_snooze_s=0)
check(r['snooze_s'] == 0 and live()[2] is False, 'unit clear works')
r = sync(p_snooze_s=999999)
check(r['snooze_s'] <= 28800, 'unit snooze capped at 8 h')
sync(p_snooze_s=0)

# 5. Snooze from the app
auth.execute("select set_snooze('Main Fridge', 30, 'add')")
until = auth.fetchone()[0]
r = sync()
check(1795 <= r['snooze_s'] <= 1800 and live()[4] == 'app', 'app snooze reaches unit (%s s)' % r['snooze_s'])
auth.execute("select set_snooze('Main Fridge', 15, 'add')")
r = sync()
check(2695 <= r['snooze_s'] <= 2700, 'app add extends running snooze (%s s)' % r['snooze_s'])
auth.execute("select set_snooze('Main Fridge', 10, 'set')")
r = sync()
check(595 <= r['snooze_s'] <= 600, 'app set replaces (%s s)' % r['snooze_s'])
auth.execute("select set_snooze('Main Fridge', 0, 'set')")
r = sync()
check(r['snooze_s'] == 0 and live()[3] is None, 'app clear reaches unit')
for bad in [-1, 481]:
    try:
        auth.execute("select set_snooze('Main Fridge', %s, 'add')", (bad,))
        check(False, 'rejects snooze %s' % bad)
    except psycopg2.Error:
        check(True, 'rejects snooze %s' % bad)
try:
    auth.execute("select set_snooze('Nope', 30, 'add')")
    check(False, 'unknown unit rejected')
except psycopg2.Error:
    check(True, 'unknown unit rejected')

# 6. Expiry uses server time
cur.execute("update temperature_live set snooze_until=now()-interval '1 second', snoozed=true where unit_name='Main Fridge'")
r = sync()
check(r['snooze_s'] == 0 and live()[2] is False and live()[3] is None, 'expired snooze cleared by server')

# 7. Defrost
auth.execute("select set_defrost('Main Fridge', true, 120)")
r = sync()
check(r['defrost'] is True and 7190 <= r['defrost_s'] <= 7200, 'defrost reaches unit (%s s)' % r['defrost_s'])
cur.execute("update temperature_live set defrost_until=now()-interval '1 second' where unit_name='Main Fridge'")
r = sync()
check(r['defrost'] is False and live()[5] is False, 'defrost expires by itself')
auth.execute("select set_defrost('Main Fridge', true, 120)")
auth.execute("select set_defrost('Main Fridge', false)")
r = sync()
check(r['defrost'] is False and r['defrost_s'] == 0, 'defrost off from app')

# 8. Settings
auth.execute("select update_unit_settings('Main Fridge','Main Fridge','Unit',5,8,120)")
r = sync(p_temp=6)
check(float(r['warn']) == 5 and float(r['crit']) == 8 and r['delay_s'] == 120 and r['alert'] == 'warning', 'settings reach unit')
try:
    auth.execute("select update_unit_settings('Main Fridge','Main Fridge','Unit',8,5,120)")
    check(False, 'warn>=crit rejected')
except psycopg2.Error:
    check(True, 'warn>=crit rejected')

# 9. Rename keeps unit attached
auth.execute("select update_unit_settings('Main Fridge','Prep Fridge','Unit',5,8,120)")
r = sync()
check(r['name'] == 'Prep Fridge' and live('Prep Fridge') is not None and live('Main Fridge') is None, 'rename follows live row')
auth.execute("select update_unit_settings('Prep Fridge','Main Fridge','Unit',11,15,10)")

# 10. Firmware target
cur.execute("insert into firmware_releases(version, files) values ('2.0.1', '[]')")
auth.execute("select set_fw_target('2.0.1', array['Main Fridge'])")
check(auth.fetchone()[0] == 1, 'fw target set on one unit')
check(sync()['fw_target'] == '2.0.1', 'fw target reaches unit')
try:
    auth.execute("select set_fw_target('9.9.9', null)")
    check(False, 'unknown version rejected')
except psycopg2.Error:
    check(True, 'unknown version rejected')
auth.execute("select set_fw_target(null, null)")
check(auth.fetchone()[0] == 3 and sync()['fw_target'] is None, 'fw target cleared on all')

# 11. Permissions
for fn in ["set_snooze('Main Fridge',30,'add')", "set_defrost('Main Fridge',true,120)",
           "set_fw_target(null,null)", "update_unit_settings('Main Fridge','x','y',1,2,3)"]:
    try:
        anon.execute('select ' + fn)
        check(False, 'anon cannot call ' + fn.split('(')[0])
    except psycopg2.Error as e:
        check(e.pgcode == '42501', 'anon cannot call %s' % fn.split('(')[0])

# 12. New unit with no live row yet: first sync creates it
cur.execute("insert into units(name, location, warn_temp, critical_temp, device_id, device_key_hash) "
            "values ('Meat Fridge','Unit',5,8,'tms-04',%s)", (hashlib.sha256(b'k4').hexdigest(),))
r = sync(p_device='tms-04', p_key='k4', p_temp=3.0)
check(r['name'] == 'Meat Fridge' and live('Meat Fridge')[1] == 'ok', 'new unit live row created on first sync')

# 13. Write gating: tiny changes are not written; heartbeat and real changes are
sync(p_device='tms-04', p_key='k4', p_temp=3.0)
cur.execute("select updated_at from temperature_live where unit_name='Meat Fridge'")
t1 = cur.fetchone()[0]
sync(p_device='tms-04', p_key='k4', p_temp=3.1)
cur.execute("select updated_at, temp from temperature_live where unit_name='Meat Fridge'")
t2, tp = cur.fetchone()
check(t2 == t1 and tp == 3.0, '0.1 C change within 30 s not written')
sync(p_device='tms-04', p_key='k4', p_temp=3.3)
cur.execute("select temp from temperature_live where unit_name='Meat Fridge'")
check(cur.fetchone()[0] == 3.3, '0.3 C change written')
cur.execute("update temperature_live set last_seen=now()-interval '31 seconds' where unit_name='Meat Fridge'")
sync(p_device='tms-04', p_key='k4', p_temp=3.3)
cur.execute("select now()-last_seen < interval '2 seconds' from temperature_live where unit_name='Meat Fridge'")
check(cur.fetchone()[0], 'heartbeat written after 30 s')
auth.execute("select set_defrost('Meat Fridge', true, 120)")
cur.execute("update temperature_live set defrost_until=now()-interval '1 second' where unit_name='Meat Fridge'")
r = sync(p_device='tms-04', p_key='k4', p_temp=3.3)
cur.execute("select defrost_mode from temperature_live where unit_name='Meat Fridge'")
check(r['defrost'] is False and cur.fetchone()[0] is False, 'defrost expiry written even with no reading change')
cur.execute("update temperature_live set snooze_until=now()+interval '5 min', snoozed=true where unit_name='Meat Fridge'")
cur.execute("update temperature_live set snooze_until=now()-interval '1 second' where unit_name='Meat Fridge'")
sync(p_device='tms-04', p_key='k4', p_temp=3.3)
cur.execute("select snoozed, snooze_until from temperature_live where unit_name='Meat Fridge'")
check(cur.fetchone() == (False, None), 'snooze expiry written even with no reading change')

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
