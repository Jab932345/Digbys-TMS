"""End-to-end test: real firmware (simulated hardware) <-> real gateway <-> fake
Supabase backed by a real Postgres with the real migration.

    python3 tests/test_e2e.py
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = os.path.join(ROOT, 'tests')
PG = 'host=/tmp port=55432 user=postgres'
SUPA_PORT, GW_PORT = 58001, 58787
ANON = 'test-anon-key'
KEY = 'unit-key-abc'
WORK = tempfile.mkdtemp(prefix='tms-e2e-')
DEV = os.path.join(WORK, 'board')
STORE = os.path.join(WORK, 'storage')
CTL = os.path.join(WORK, 'control.json')
STATUS = os.path.join(WORK, 'status.json')
fails = 0
procs = {}
# MICROPYTHON=<path> runs the firmware on MicroPython (32-bit, ESP8266 language
# limits, 38 KB heap like the D1 Mini) using the same .mpy files a unit gets.
# Without it the firmware runs on CPython (faster, and tests the 12-day tick wrap).
MP = os.environ.get('MICROPYTHON')
HEAP = os.environ.get('HEAP', '38k')
APP = ['node', 'link', 'net', 'disp', 'probe']
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import build_mpy  # noqa: E402


def check(cond, what):
    global fails
    print(('PASS ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        fails += 1
    return cond


def wait(pred, timeout, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


# ── database ────────────────────────────────────────────────────────────
def psql(db, f):
    subprocess.run(['psql', '-h', '/tmp', '-p', '55432', '-U', 'postgres', '-d', db, '-q',
                    '-v', 'ON_ERROR_STOP=1', '-f', f], check=True, capture_output=True)


c = psycopg2.connect(PG + ' dbname=postgres')
c.autocommit = True
c.cursor().execute('drop database if exists tms')
c.cursor().execute('create database tms')
c.close()
psql('tms', os.path.join(T, 'supabase_stub.sql'))
psql('tms', os.path.join(ROOT, 'supabase', '001_sync_ota.sql'))
db = psycopg2.connect(PG + ' dbname=tms')
db.autocommit = True
cur = db.cursor()
cur.execute("update units set device_id='tms-02', device_key_hash=%s, alarm_delay_s=4, sync_interval_s=2, "
            "log_interval_s=10 where name='Main Fridge'", (hashlib.sha256(KEY.encode()).hexdigest(),))
app = psycopg2.connect(PG + ' dbname=tms')
app.autocommit = True
appc = app.cursor()
appc.execute('set role authenticated')


def live():
    cur.execute("select temp, alert, snooze_until, snooze_source, defrost_mode, siren, fw_version, fw_status, "
                "last_seen from temperature_live where unit_name='Main Fridge'")
    r = cur.fetchone()
    return dict(zip(['temp', 'alert', 'snooze_until', 'src', 'defrost', 'siren', 'fw', 'fws', 'seen'], r)) if r else None


def app_rpc(sql, *a):
    appc.execute('select ' + sql, a)
    return appc.fetchone()[0]


# ── processes ───────────────────────────────────────────────────────────
def start_supa():
    env = dict(os.environ, STORAGE_DIR=STORE, ANON_KEY=ANON)
    procs['supa'] = subprocess.Popen([sys.executable, os.path.join(T, 'fake_supabase.py'), str(SUPA_PORT)], env=env)


def start_gw():
    env = dict(os.environ, SUPABASE_URL='http://127.0.0.1:%d' % SUPA_PORT, SUPABASE_ANON_KEY=ANON,
               PORT=str(GW_PORT), CACHE_DIR=os.path.join(WORK, 'gwcache'))
    procs['gw'] = subprocess.Popen([sys.executable, os.path.join(ROOT, 'gateway', 'tms_gateway.py')], env=env,
                                   stderr=open(os.path.join(WORK, 'gw.log'), 'a'))
    wait(lambda: urllib.request.urlopen('http://127.0.0.1:%d/health' % GW_PORT, timeout=1).status == 200, 10)


def stop(name):
    p = procs.pop(name, None)
    if p:
        p.terminate()
        p.wait()


boots = []
dev_run = [True]


def device_loop():
    env = dict(os.environ, SIM_CTL=CTL, SIM_STATUS=STATUS,
               SIM_TICKS_START=str((1 << 30) - 25000))      # tick counter wraps 25 s in
    log = open(os.path.join(WORK, 'device.log'), 'a')
    while dev_run[0]:
        cmd = ([MP, '-X', 'heapsize=' + HEAP, os.path.join(T, 'mp_sim', 'sim_main.py')] if MP
               else [sys.executable, os.path.join(T, 'sim_device.py')])
        env['SIM_SHIMS'] = os.path.join(T, 'mp_sim')
        p = subprocess.Popen(cmd, cwd=DEV, env=env, stdout=log, stderr=log)
        procs['dev'] = p
        code = p.wait()
        boots.append((time.time(), code))
        if code != 99:
            break


def ctl(**kw):
    cur_ctl = {}
    if os.path.exists(CTL):
        cur_ctl = json.load(open(CTL))
    cur_ctl.update(kw)
    with open(CTL + '.tmp', 'w') as f:
        json.dump(cur_ctl, f)
    os.replace(CTL + '.tmp', CTL)


press_id = [0]


def press(ms):
    press_id[0] += 1
    c_ = json.load(open(CTL))
    c_.setdefault('press', []).append({'id': press_id[0], 'ms': ms})
    ctl(press=c_['press'])


def status():
    return json.load(open(STATUS))


def siren():
    return status().get('siren') is True


def seen_display(pred, timeout):
    return wait(lambda: pred(status().get('display', '')), timeout, 0.05)


# ── board filesystem ────────────────────────────────────────────────────
os.makedirs(DEV)
os.makedirs(STORE)
if MP:
    out = build_mpy.build()
    for f in ('boot.py', 'main.py', 'selftest.py'):
        shutil.copy(os.path.join(ROOT, 'firmware', f), DEV)
    for m in APP + ['ota']:
        shutil.copy(os.path.join(out, m + '.mpy'), DEV)
else:
    for f in os.listdir(os.path.join(ROOT, 'firmware')):
        if f.endswith('.py') and f != 'config.example.py':
            shutil.copy(os.path.join(ROOT, 'firmware', f), DEV)
cfg = open(os.path.join(ROOT, 'firmware', 'config.example.py')).read()
cfg = cfg.replace("DEVICE_ID = 'tms-01'", "DEVICE_ID = 'tms-02'").replace("DEVICE_KEY = 'replace-me'", "DEVICE_KEY = %r" % KEY)
cfg = cfg.replace("GATEWAY = 'http://192.168.7.131:8787'", "GATEWAY = 'http://127.0.0.1:%d'" % GW_PORT)
assert 'tms-02' in cfg and KEY in cfg and str(GW_PORT) in cfg
open(os.path.join(DEV, 'config.py'), 'w').write(cfg)
open(os.path.join(DEV, 'fw.json'), 'w').write('{"v": "2.0.0"}')
ctl(temp=4.0, probe=True, wifi=True, press=[])


def board(name):
    return open(os.path.join(DEV, name)).read()


def board_bytes(name):
    return open(os.path.join(DEV, name), 'rb').read()


def publish(ver, mutate=None, corrupt=None):
    # Same manifest format as tools/publish.py.
    d = os.path.join(STORE, ver)
    os.makedirs(d)
    files = []
    src_dir = os.path.join(WORK, 'src-' + ver)
    os.makedirs(src_dir)
    exe = build_mpy.mpy_cross()
    for m in APP:
        src = open(os.path.join(ROOT, 'firmware', m + '.py'), 'rb').read()
        if mutate and m + '.py' in mutate:
            src = mutate[m + '.py'](src)
        sp = os.path.join(src_dir, m + '.py')
        open(sp, 'wb').write(src)
        n = m + '.mpy'
        subprocess.run([exe, '-o', os.path.join(d, n), sp], check=True)
        data = open(os.path.join(d, n), 'rb').read()
        h = hashlib.sha256(data).hexdigest()
        if corrupt == m + '.py':
            h = '0' * 64
        files.append({'n': n, 's': len(data), 'h': h})
    open(os.path.join(d, 'manifest.json'), 'w').write(json.dumps({'version': ver, 'files': files}))
    cur.execute('insert into firmware_releases(version, files) values (%s, %s)', (ver, json.dumps(files)))


try:
    start_supa()
    time.sleep(0.5)
    start_gw()
    threading.Thread(target=device_loop, daemon=True).start()

    print('\n-- normal running')
    check(wait(lambda: live() and live()['temp'] == 4.0 and live()['fw'] == '2.0.0', 15), 'unit reports reading and version')
    check(seen_display(lambda d: d == ' 4*C', 5), 'display shows " 4*C" (no-decimal display)')
    check(not siren(), 'siren off at 4 C')

    print('\n-- alarm')
    ctl(temp=16.0)
    t0 = time.time()
    check(wait(siren, 15), 'siren sounds when critical')
    el = time.time() - t0
    check(3.5 <= el <= 8, 'siren waits for the alarm delay (%.1f s, delay 4 s)' % el)
    check(wait(lambda: live()['siren'] is True and live()['alert'] == 'critical', 6), 'website sees siren + critical')

    print('\n-- snooze from the website')
    app_rpc("set_snooze('Main Fridge', 30, 'add')")
    t0 = time.time()
    check(wait(lambda: not siren(), 6), 'unit siren stops after app snooze (%.1f s)' % (time.time() - t0))
    check(seen_display(lambda d: d[:2] in ('Sn', '5n'), 6), 'unit display shows snooze time')   # S and 5 share segments
    check(wait(lambda: live()['siren'] is False, 6), 'website sees siren off')

    print('\n-- clear from the website')
    app_rpc("set_snooze('Main Fridge', 0, 'set')")
    check(wait(siren, 6), 'siren returns immediately after app clears snooze (still critical)')

    print('\n-- snooze from the unit button')
    t0 = time.time()
    press(300)
    check(wait(lambda: not siren(), 2), 'button silences siren locally (%.1f s)' % (time.time() - t0))
    check(wait(lambda: live()['snooze_until'] is not None and live()['src'] == 'unit', 5), 'website shows unit snooze')
    r = live()
    left = (r['snooze_until'] - r['seen']).total_seconds() if r['snooze_until'] else 0
    check(1780 <= left <= 1801, 'website snooze is 30 min (%.0f s)' % left)
    time.sleep(3)
    check(not siren() and live()['snooze_until'] is not None, 'unit snooze survives later syncs')

    print('\n-- long press clears')
    press(2800)
    check(wait(lambda: live()['snooze_until'] is None, 8), 'long press clears snooze on website')
    check(wait(siren, 4), 'siren back after long press')

    print('\n-- defrost')
    app_rpc("set_defrost('Main Fridge', true, 120)")
    check(wait(lambda: not siren(), 6), 'defrost from website silences unit')
    check(seen_display(lambda d: d == 'dEF ', 6), 'unit display shows dEF')
    app_rpc("set_defrost('Main Fridge', false, 120)")
    check(wait(siren, 6), 'defrost off -> siren back')

    print('\n-- back to normal')
    ctl(temp=4.0)
    check(wait(lambda: not siren(), 8), 'siren stops when temperature recovers')
    check(wait(lambda: live()['alert'] == 'ok', 6), 'website back to ok')

    print('\n-- thresholds edited on the website')
    app_rpc("update_unit_settings('Main Fridge','Main Fridge','Unit',2,3,4)")
    check(wait(siren, 15), 'unit follows new thresholds (4 C now critical)')
    check(wait(lambda: json.loads(board('cache.json')) == {'w': 2.0, 'c': 3.0, 'd': 4}, 5), 'thresholds cached on board')
    app_rpc("update_unit_settings('Main Fridge','Main Fridge','Unit',11,15,4)")
    check(wait(lambda: not siren(), 8), 'restored thresholds -> quiet')

    print('\n-- gateway down')
    stop('gw')
    ctl(temp=16.0)
    check(wait(siren, 15), 'alarm still works with no gateway')
    press(300)
    check(wait(lambda: not siren(), 2), 'button works offline')
    check(seen_display(lambda d: d == 'nEt ', 45), 'display warns nEt when offline (siren silenced)')
    time.sleep(1)
    start_gw()
    t0 = time.time()
    ok = wait(lambda: live()['snooze_until'] is not None and live()['src'] == 'unit', 45)
    check(ok, 'offline button press reaches website when gateway returns (%.0f s, back-off max 30 s)' % (time.time() - t0))
    app_rpc("set_snooze('Main Fridge', 0, 'set')")
    ctl(temp=4.0)
    check(wait(lambda: not siren() and live()['alert'] == 'ok', 10), 'clean state')

    print('\n-- probe fault')
    ctl(probe=False)
    check(wait(lambda: live()['temp'] is None and live()['alert'] == 'critical', 30), 'website shows probe fault as critical')
    check(seen_display(lambda d: d == 'Err ', 5), 'display shows Err')
    check(wait(siren, 10), 'siren for probe fault')
    ctl(probe=True)
    check(wait(lambda: not siren() and live()['temp'] == 4.0, 15), 'recovers when probe returns')

    if MP:
        print('\n-- OTA: good update')
        nboot = len(boots)
        publish('2.0.1', mutate={'node.py': lambda d: d + b'\nRELEASE = "rel-2.0.1"\n'})
        app_rpc("set_fw_target('2.0.1', array['Main Fridge'])")
        t0 = time.time()
        check(wait(lambda: len(boots) > nboot, 60), 'unit restarts to install (%.1f s)' % (time.time() - t0))
        check(wait(lambda: live()['fw'] == '2.0.1', 20), 'website shows 2.0.1 running')
        check(wait(lambda: json.loads(board('fw.json')).get('trial') is None, 10), 'new version confirmed (not trial)')
        check(b'rel-2.0.1' in board_bytes('node.mpy'), 'new node.mpy installed')
        check(not any(f.endswith('.bak') or f.endswith('.new') for f in os.listdir(DEV)), 'no leftover .bak/.new files')
        check(wait(lambda: (live()['fws'] or '').startswith('installed 2.0.1'), 10), 'website shows "installed 2.0.1"')

        print('\n-- OTA: broken release is rolled back')
        nboot = len(boots)
        publish('2.0.2', mutate={'node.py': lambda d: b'raise RuntimeError("broken build")\n' + d})
        app_rpc("set_fw_target('2.0.2', array['Main Fridge'])")
        check(wait(lambda: json.loads(board('fw.json'))['v'] == '2.0.1' and '2.0.2' in json.loads(board('fw.json')).get('bad', []), 60),
              'broken 2.0.2 rolled back to 2.0.1')
        check(len(boots) - nboot >= 4, 'took the expected restarts (%d)' % (len(boots) - nboot))
        check(b'rel-2.0.1' in board_bytes('node.mpy') and b'broken build' not in board_bytes('node.mpy'), 'old node.mpy restored')
        check(wait(lambda: live()['fw'] == '2.0.1' and 'rolled back 2.0.2' in (live()['fws'] or ''), 15), 'website shows rollback')
        nboot = len(boots)
        time.sleep(10)
        check(len(boots) == nboot, 'does not retry a failed version')

        print('\n-- OTA: corrupt file refused')
        publish('2.0.3', corrupt='disp.py')
        nboot = len(boots)
        before = board_bytes('disp.mpy')
        app_rpc("set_fw_target('2.0.3', array['Main Fridge'])")
        check(wait(lambda: 'fail 2.0.3' in (live()['fws'] or ''), 20), 'website shows failed download')
        check(board_bytes('disp.mpy') == before and live()['fw'] == '2.0.1', 'running version untouched')
        check(not any(f.endswith('.new') for f in os.listdir(DEV)), 'partial download cleaned up')
        check(len(boots) - nboot == 1, 'one restart for the attempt (%d)' % (len(boots) - nboot))
        nboot = len(boots)
        time.sleep(12)
        check(len(boots) == nboot, 'waits 10 min before retrying a failed download')
        app_rpc("set_fw_target(null, null)")

        print('\n-- OTA waits while alarming')
        publish('2.0.4', mutate={'node.py': lambda d: d + b'\nRELEASE = "rel-2.0.4"\n'})
        ctl(temp=16.0)
        check(wait(siren, 15), 'alarming')
        nboot = len(boots)
        app_rpc("set_fw_target('2.0.4', null)")
        time.sleep(8)
        check(len(boots) == nboot, 'no update during alarm')
        ctl(temp=4.0)
        check(wait(lambda: live()['fw'] == '2.0.4', 40), 'updates once the alarm clears')

    print('\n-- gateway health')
    h = json.load(urllib.request.urlopen('http://127.0.0.1:%d/health' % GW_PORT))
    check(h['ok'] and h['supabase_ok_s_ago'] is not None and h['supabase_ok_s_ago'] < 10, 'health ok %s' % h)

    print('\n-- HACCP log volume')
    cur.execute("select count(*), min(created_at), max(created_at) from temperature_logs where unit_name='Main Fridge'")
    n, a, b = cur.fetchone()
    mins = (b - a).total_seconds() / 60
    check(n <= mins * 6 + 40, '%d log rows over %.1f min (interval 10 s + alert changes)' % (n, mins))
finally:
    dev_run[0] = False
    for k in list(procs):
        stop(k)
    print('\nwork dir:', WORK)
    print('%d failure(s)' % fails)
    sys.exit(1 if fails else 0)
