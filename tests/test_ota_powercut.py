"""Power cut at every possible point of an install: after boot_check the board
must hold a complete old or complete new set of files, never a mix, and a
failed new version must roll back to the complete old set."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'firmware'))


class Reset(Exception):
    pass


class PowerCut(Exception):
    pass


def _reset():
    raise Reset()


sys.modules['machine'] = types.SimpleNamespace(reset=_reset)
import ota  # noqa: E402

fails = 0


def check(cond, what):
    global fails
    print(('PASS ' if cond else 'FAIL ') + what)
    if not cond:
        fails += 1


OLD = {'node.mpy': b'old-node', 'net.mpy': b'old-net', 'probe.py': b'old-probe-py'}
NEW = {'node.mpy': b'new-node', 'net.mpy': b'new-net', 'probe.mpy': b'new-probe'}


def files():
    return {f: open(f, 'rb').read() for f in os.listdir('.') if f.endswith(('.mpy', '.py'))}


def manifest():
    return {'version': '2', 'files': [{'n': n, 's': len(d), 'h': hashlib.sha256(d).hexdigest()} for n, d in NEW.items()]}


def get_json(path):
    return manifest()


def get_to_file(path, fname, hasher, max_len):
    d = NEW[path.split('/')[-1]]
    open(fname, 'wb').write(d)
    hasher.update(d)
    return len(d)


def run_case(cut_at, op_name):
    d = tempfile.mkdtemp()
    os.chdir(d)
    for n, b in OLD.items():
        open(n, 'wb').write(b)
    json.dump({'v': '1'}, open('fw.json', 'w'))

    real = {'rename': os.rename, 'remove': os.remove}
    count = [0]

    def wrap(name):
        def f(*a):
            count[0] += 1
            if count[0] == cut_at:
                raise PowerCut()
            return real[name](*a)
        return f

    os.rename, os.remove = wrap('rename'), wrap('remove')
    cut = False
    try:
        ota.install('2', get_json, get_to_file)
    except PowerCut:
        cut = True
    except Reset:
        pass
    finally:
        os.rename, os.remove = real['rename'], real['remove']

    # Power returns: boot_check runs, then each check
    try:
        ota.boot_check()
    except Exception as e:
        check(False, '[%s %d] boot_check raised %r' % (op_name, cut_at, e))
        return False
    st = json.load(open('fw.json'))
    f = files()
    live = {k: v for k, v in f.items() if not k.endswith(('.bak', '.new'))}
    # What Python would import for each module (.py wins over .mpy)
    imp = {}
    for m in ('node', 'net', 'probe'):
        imp[m] = live.get(m + '.py', live.get(m + '.mpy'))
    old_imp = {'node': b'old-node', 'net': b'old-net', 'probe': b'old-probe-py'}
    new_imp = {'node': b'new-node', 'net': b'new-net', 'probe': b'new-probe'}
    if st.get('v') == '2':
        ok = imp == new_imp
        what = 'new'
    else:
        ok = imp == old_imp and st.get('v') == '1'
        what = 'old'
    check(ok, '[cut at op %d] boots a complete %s set %s' % (cut_at, what, imp if not ok else ''))

    # If it is on the new version, simulate it failing every boot -> must roll back to complete old set
    if st.get('v') == '2' and st.get('trial'):
        for _ in range(ota.MAX_TRIAL_BOOTS + 1):
            ota.boot_check()
        f = {k: v for k, v in files().items() if not k.endswith(('.bak', '.new'))}
        imp = {m: f.get(m + '.py', f.get(m + '.mpy')) for m in ('node', 'net', 'probe')}
        st = json.load(open('fw.json'))
        check(imp == old_imp and st['v'] == '1' and '2' in st['bad'], '[cut at op %d] rollback restores complete old set' % cut_at)
    shutil.rmtree(d)
    return cut


n = 1
while True:
    cut = run_case(n, 'install')
    if not cut:
        break
    n += 1
print('tested power cuts at %d points' % (n - 1))
print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
