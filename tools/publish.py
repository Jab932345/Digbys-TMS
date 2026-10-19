#!/usr/bin/env python3
"""Publish a firmware version so units can update themselves over Wi-Fi.

    python3 tools/publish.py 2.0.1 --notes "what changed"            # upload only
    python3 tools/publish.py 2.0.1 --target "Main Fridge"            # then update one unit
    python3 tools/publish.py 2.0.1 --target all                      # then update every unit

Upload only, then press "Update" on the website, is the safe habit: try one unit first.
Versions are never overwritten - publish a new number for every change.
"""
import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _supa  # noqa: E402
import build_mpy  # noqa: E402

FW = build_mpy.FW
MAX_FILE = 12000   # compiled size; keeps each module loadable on an ESP8266


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('version')
    ap.add_argument('--notes', default='')
    ap.add_argument('--target', help='"all" or comma-separated unit names')
    a = ap.parse_args()
    v = a.version
    if not re.match(r'^[0-9A-Za-z._-]{1,32}$', v):
        sys.exit('version: letters, digits, . _ - only (max 32)')

    if _supa.call('GET', '/rest/v1/firmware_releases?select=version&version=eq.' + _supa.q(v)):
        sys.exit('version %s already exists - use a new number' % v)

    out = build_mpy.build()                     # syntax errors stop here, before any upload
    files = []
    for m in build_mpy.APP:                      # never boot/main/ota/config
        n = m + '.mpy'
        data = open(os.path.join(out, n), 'rb').read()
        if len(data) > MAX_FILE:
            sys.exit('%s is %d bytes compiled; split it so it loads on an ESP8266' % (n, len(data)))
        files.append((n, data))

    manifest = {'version': v, 'files': [
        {'n': n, 's': len(d), 'h': hashlib.sha256(d).hexdigest()} for n, d in files]}

    for n, d in files:
        _supa.call('POST', '/storage/v1/object/firmware/%s/%s' % (v, n), d,
                   {'Content-Type': 'application/octet-stream', 'x-upsert': 'false'})
        print('uploaded', n)
    _supa.call('POST', '/storage/v1/object/firmware/%s/manifest.json' % v,
               json.dumps(manifest).encode(), {'Content-Type': 'application/json', 'x-upsert': 'false'})
    _supa.call('POST', '/rest/v1/firmware_releases',
               {'version': v, 'notes': a.notes, 'files': manifest['files']})
    with open(os.path.join(FW, 'VERSION'), 'w') as f:
        f.write(v + '\n')
    print('Published %s (%d files). firmware/VERSION set to %s.' % (v, len(files), v))

    if a.target:
        if a.target == 'all':
            path = '/rest/v1/units?active=is.true'
        else:
            names = [s.strip() for s in a.target.split(',') if s.strip()]
            path = '/rest/v1/units?name=in.(%s)' % ','.join(_supa.q('"%s"' % n) for n in names)
        rows = _supa.call('PATCH', path, {'fw_target': v}, {'Prefer': 'return=representation'})
        print('Update requested on:', ', '.join(r['name'] for r in rows) or 'no units matched')
        print('Each unit installs within a few seconds unless it is alarming; watch the website.')


if __name__ == '__main__':
    main()
