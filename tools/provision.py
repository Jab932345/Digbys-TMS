#!/usr/bin/env python3
"""Give a unit its identity and write its config file.

Existing unit (keeps its thresholds and history):
    python3 tools/provision.py --device tms-01 --name "Main Freezer" --wifi "SSID" "password"

New unit (thresholds required):
    python3 tools/provision.py --device tms-04 --name "Meat Fridge" --warn 5 --crit 8 --wifi "SSID" "password"

Writes firmware/configs/<device>.py (git-ignored: it holds the unit's key and the Wi-Fi password).
Running it again for the same unit issues a NEW key: reflash that unit afterwards.
"""
import argparse
import hashlib
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _supa  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--device', required=True, help='tms-01 .. tms-06')
    ap.add_argument('--name', required=True, help='unit name exactly as on the website')
    ap.add_argument('--location', default='')
    ap.add_argument('--warn', type=float)
    ap.add_argument('--crit', type=float)
    ap.add_argument('--wifi', nargs=2, action='append', metavar=('SSID', 'PASSWORD'), required=True)
    ap.add_argument('--gateway', default='http://192.168.8.20:8787')
    ap.add_argument('--display-dp', action='store_true', help='display has decimal points')
    ap.add_argument('--pin', nargs=2, action='append', metavar=('NAME', 'GPIO'), default=[],
                    help='override a pin, e.g. --pin PIN_RELAY 5')
    a = ap.parse_args()

    if not re.match(r'^[a-z0-9-]{3,40}$', a.device):
        sys.exit('device id: lowercase letters, digits and dashes only')
    if not re.match(r'^http://[0-9.]+:\d+$', a.gateway):
        sys.exit('gateway must look like http://192.168.8.20:8787 (an IP address, not a name)')
    for n, _ in a.pin:
        if n not in ('PIN_PROBE', 'PIN_TM_CLK', 'PIN_TM_DIO', 'PIN_RELAY', 'PIN_BUTTON'):
            sys.exit('unknown pin ' + n)

    key = secrets.token_urlsafe(24)
    khash = hashlib.sha256(key.encode()).hexdigest()

    taken = _supa.call('GET', '/rest/v1/units?select=name&device_id=eq.' + _supa.q(a.device))
    if taken and taken[0]['name'] != a.name:
        sys.exit('%s is already assigned to "%s"' % (a.device, taken[0]['name']))

    rows = _supa.call('GET', '/rest/v1/units?select=name,warn_temp,critical_temp&name=eq.' + _supa.q(a.name))
    patch = {'device_id': a.device, 'device_key_hash': khash}
    if rows:
        if a.warn is not None:
            patch['warn_temp'] = a.warn
        if a.crit is not None:
            patch['critical_temp'] = a.crit
        _supa.call('PATCH', '/rest/v1/units?name=eq.' + _supa.q(a.name), patch)
        warn = a.warn if a.warn is not None else rows[0]['warn_temp']
        crit = a.crit if a.crit is not None else rows[0]['critical_temp']
        print('Linked existing unit "%s" to %s' % (a.name, a.device))
    else:
        if a.warn is None or a.crit is None:
            sys.exit('"%s" is not on the website yet: give --warn and --crit to create it' % a.name)
        patch.update({'name': a.name, 'location': a.location, 'warn_temp': a.warn,
                      'critical_temp': a.crit, 'active': True})
        _supa.call('POST', '/rest/v1/units', patch)
        warn, crit = a.warn, a.crit
        print('Created unit "%s" as %s' % (a.name, a.device))
    if float(warn) >= float(crit):
        print('WARNING: warn (%s) is not below critical (%s) - fix it on the website' % (warn, crit))

    with open(os.path.join(ROOT, 'firmware', 'config.example.py')) as f:
        cfg = f.read()

    def setv(text, name, value):
        new, n = re.subn(r'^%s = .*$' % name, '%s = %s' % (name, value), text, count=1, flags=re.M)
        if n != 1:
            sys.exit('config.example.py has no %s line' % name)
        return new

    cfg = setv(cfg, 'DEVICE_ID', repr(a.device))
    cfg = setv(cfg, 'DEVICE_KEY', repr(key))
    cfg = setv(cfg, 'GATEWAY', repr(a.gateway))
    cfg = setv(cfg, 'DISPLAY_HAS_DP', 'True' if a.display_dp else 'False')
    cfg = setv(cfg, 'FALLBACK_WARN', repr(float(warn)))
    cfg = setv(cfg, 'FALLBACK_CRIT', repr(float(crit)))
    for n, v in a.pin:
        cfg = setv(cfg, n, str(int(v)))
    wifi = 'WIFI = [\n' + ''.join('    (%r, %r),\n' % (s, p) for s, p in a.wifi) + ']'
    cfg, n = re.subn(r'^WIFI = \[\n(?:.*\n)*?\]', wifi, cfg, count=1, flags=re.M)
    if n != 1:
        sys.exit('config.example.py WIFI block not found')

    out_dir = os.path.join(ROOT, 'firmware', 'configs')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, a.device + '.py')
    with open(out, 'w') as f:
        f.write(cfg)
    os.chmod(out, 0o600)
    print('Wrote', os.path.relpath(out, ROOT))
    print('Next: bash tools/flash.sh <usb-port> %s' % a.device)


if __name__ == '__main__':
    main()
