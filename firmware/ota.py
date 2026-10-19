# Over-the-air updates with automatic rollback. Flashed by USB only; never
# replaced over the air, so a bad update can always be undone.
#
# fw.json keeps: v (running version), prev, trial (new version not yet proven),
# boots (boots since install), bak (files with a .bak copy), new (files that
# did not exist before), bad (versions that failed and must not be retried),
# req (version to install on the next boot), tries (download attempts per version).
#
# Installs run from boot.py, before the app is loaded, so the download has the
# whole heap to itself (the app plus a download does not fit in an ESP8266).
import os
import gc
import hashlib
import binascii
import machine

try:
    import ujson as json
except ImportError:
    import json

# App files are precompiled .mpy (an ESP8266 does not have the memory to compile
# them). These are never replaced over the air.
PROTECTED = ('boot.py', 'main.py', 'ota.py', 'ota.mpy', 'config.py', 'selftest.py',
             'fw.json', 'cache.json')
MAX_TRIAL_BOOTS = 3
MAX_TRIES = 3
confirmed = False


def _load():
    # fw.json.tmp is the fallback if power was cut between writing and renaming.
    for n in ('fw.json', 'fw.json.tmp'):
        try:
            with open(n) as f:
                st = json.load(f)
            if isinstance(st, dict) and st.get('v'):
                return st
        except Exception:
            pass
    return {'v': 'usb'}


def _save(st):
    # The ESP8266 FAT filesystem will not rename over an existing file.
    with open('fw.json.tmp', 'w') as f:
        json.dump(st, f)
    _rm('fw.json')
    os.rename('fw.json.tmp', 'fw.json')


def _exists(n):
    try:
        os.stat(n)
        return True
    except OSError:
        return False


def _rm(n):
    try:
        os.remove(n)
    except OSError:
        pass


def version():
    return _load().get('v', 'usb')


def bad_versions():
    return _load().get('bad', [])


def in_trial():
    return bool(_load().get('trial'))


def in_request():
    return bool(_load().get('req'))


def boot_check():
    # Called from boot.py on every start. Counts boots of an unproven version
    # and rolls back after MAX_TRIAL_BOOTS. Returns a status string.
    st = _load()
    if not st.get('trial'):
        return 'ok'
    # Finish a swap that a power cut interrupted.
    for n in st.get('bak', []) + st.get('new', []):
        if _exists(n + '.new'):
            if _exists(n) and not _exists(n + '.bak'):
                os.rename(n, n + '.bak')
            _rm(n)
            os.rename(n + '.new', n)
        elif n.endswith('.py') and _exists(n) and not _exists(n + '.bak'):
            os.rename(n, n + '.bak')            # old .py would shadow the new .mpy
    st['boots'] = st.get('boots', 0) + 1
    if st['boots'] <= MAX_TRIAL_BOOTS:
        _save(st)
        return 'trial %d' % st['boots']
    failed = st.get('v')
    for n in st.get('bak', []):
        if _exists(n + '.bak'):
            _rm(n)
            os.rename(n + '.bak', n)
    for n in st.get('new', []):
        _rm(n)
    bad = st.get('bad', [])
    if failed and failed not in bad:
        bad.append(failed)
    _save({'v': st.get('prev', 'usb'), 'bad': bad[-5:], 'last': 'rolled back ' + str(failed)})
    return 'rolled back'


def confirm():
    # Called by the app after its first successful sync on a new version.
    global confirmed
    confirmed = True
    st = _load()
    if not st.get('trial'):
        return
    for n in st.get('bak', []):
        _rm(n + '.bak')
    _save({'v': st.get('v'), 'bad': st.get('bad', []), 'last': 'installed ' + str(st.get('v'))})


def last_event():
    return _load().get('last')


def tries(target):
    return _load().get('tries', {}).get(target, 0)


def request(target):
    # Called by the app: install `target` on the next boot, then restart now.
    st = _load()
    st['req'] = target
    _save(st)
    machine.reset()


def run_request(net):
    # Called from boot.py. Returns a status string, or resets on success.
    st = _load()
    target = st.pop('req', None)
    if not target:
        return None
    t = st.get('tries', {})
    t = {target: t.get(target, 0) + 1}          # only the current target is tracked
    st['tries'] = t
    _save(st)
    try:
        import time
        net.setup()
        for _ in range(100):                    # up to 20 s for Wi-Fi
            if net.up():
                break
            time.sleep_ms(200)
        gc.collect()
        err = install(target, net.get_json, net.get_to_file)   # resets on success
    except Exception as e:
        err = 'error %s' % e
    st = _load()
    st['last'] = ('fail %s (try %d of %d): %s' % (target, t[target], MAX_TRIES, err))[:120]
    _save(st)
    return st['last']


def trial_timeout(_t=None):
    # Timer callback from main.py: a trial version that has not synced within
    # the window is treated as broken. Resetting counts towards rollback.
    if not confirmed:
        machine.reset()


def _ok_name(n):
    if not isinstance(n, str) or n in PROTECTED or not n.endswith('.mpy'):
        return False
    base = n[:-4]
    if not base or len(base) > 20:
        return False
    for c in base:
        if not (c.isalpha() or c.isdigit() or c == '_'):
            return False
    return base == base.lower()


def install(target, get_json, get_to_file):
    # Downloads, verifies and installs `target`, then resets. Returns an error
    # string only if it did not install (the running version is untouched).
    gc.collect()
    st = _load()
    if target == st.get('v'):
        return 'already on ' + target
    if target in st.get('bad', []):
        return 'skipped bad ' + target
    try:
        man = get_json('/fw/%s/manifest.json' % target)
    except Exception as e:
        return 'manifest: %s' % e
    if not isinstance(man, dict) or man.get('version') != target:
        return 'manifest invalid'
    files = man.get('files')
    if not isinstance(files, list) or not files or len(files) > 12:
        return 'manifest invalid'
    total = 0
    for f in files:
        if not isinstance(f, dict) or not _ok_name(f.get('n')) \
                or not isinstance(f.get('s'), int) or not isinstance(f.get('h'), str) or len(f['h']) != 64:
            return 'manifest bad entry'
        total += f['s']
    try:
        fs = os.statvfs('/')
        free = fs[0] * fs[4]
        if free < total * 2 + 8192:
            return 'no space: need %d have %d' % (total * 2 + 8192, free)
    except Exception:
        pass

    # 1. Download everything to .new and verify each hash. Nothing live changes yet.
    done = []
    try:
        for f in files:
            h = hashlib.sha256()
            n = get_to_file('/fw/%s/%s' % (target, f['n']), f['n'] + '.new', h, f['s'] + 1)
            if n != f['s'] or binascii.hexlify(h.digest()).decode() != f['h']:
                done.append(f['n'])
                raise ValueError('hash mismatch ' + f['n'])
            done.append(f['n'])
            gc.collect()
    except Exception as e:
        for n in done:
            _rm(n + '.new')
        for f in files:
            _rm(f['n'] + '.new')
        return 'download: %s' % e

    # 2. Swap in. Record what was backed up first, so boot_check can undo it.
    # A same-named .py would be imported instead of the new .mpy, so it is
    # moved aside too (and put back if this version is rolled back).
    bak, new = [], []
    for f in files:
        (bak if _exists(f['n']) else new).append(f['n'])
        py = f['n'][:-4] + '.py'
        if _exists(py):
            bak.append(py)
    _save({'v': target, 'prev': st.get('v', 'usb'), 'trial': True, 'boots': 0,
           'bak': bak, 'new': new, 'bad': st.get('bad', []), 'tries': st.get('tries', {})})
    for n in bak:
        _rm(n + '.bak')
        os.rename(n, n + '.bak')
    for f in files:
        os.rename(f['n'] + '.new', f['n'])
    machine.reset()
