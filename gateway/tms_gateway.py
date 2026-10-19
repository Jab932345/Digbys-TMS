#!/usr/bin/env python3
"""Digby's TMS gateway — runs on the Raspberry Pi in the unit.

The D1 Minis cannot hold an HTTPS connection to Supabase (their TLS library is
too small for Cloudflare). They talk plain HTTP to this gateway on the local
network; the gateway talks HTTPS to Supabase and serves firmware updates.

Endpoints (all JSON, all with Content-Length):
  POST /sync                      unit reading + button action -> shared state
  GET  /fw/<version>/<file>       firmware files, verified against the manifest
  GET  /health                    gateway status

Python 3 standard library only. Settings come from environment variables
(see tms-gateway.env).
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
PORT = int(os.environ.get('PORT', '8787'))
CACHE = os.environ.get('CACHE_DIR', '/var/lib/tms-gateway')
UPSTREAM_TIMEOUT = float(os.environ.get('UPSTREAM_TIMEOUT', '3.5'))
VERSION = '2.0.0'

RE_VER = re.compile(r'^[0-9A-Za-z._-]{1,32}$')
RE_FILE = re.compile(r'^[a-z0-9_]{1,20}\.mpy$')
_state = {'last_ok': None, 'last_err': None, 'started': time.time()}
_fw_lock = threading.Lock()


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, file=sys.stderr, flush=True)


def _upstream(method, path, body=None, timeout=UPSTREAM_TIMEOUT):
    req = urllib.request.Request(SUPABASE_URL + path, method=method, data=body)
    req.add_header('apikey', ANON_KEY)
    req.add_header('Authorization', 'Bearer ' + ANON_KEY)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── /sync ────────────────────────────────────────────────────────────────
def _num(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError('bad number')
    return v


def build_rpc(b):
    if not isinstance(b, dict):
        raise ValueError('body must be an object')
    d, k = b.get('d'), b.get('k')
    if not isinstance(d, str) or not 0 < len(d) <= 40 or not isinstance(k, str) or not 0 < len(k) <= 100:
        raise ValueError('device id/key missing')
    snz = b.get('snz')
    if snz is not None:
        if isinstance(snz, bool) or not isinstance(snz, int) or not 0 <= snz <= 28800:
            raise ValueError('bad snz')
    rssi = b.get('rssi')
    if rssi is not None and (isinstance(rssi, bool) or not isinstance(rssi, int)):
        rssi = None
    fw = b.get('fw')
    fws = b.get('fws')
    return {
        'p_device': d,
        'p_key': k,
        'p_temp': _num(b.get('t')),
        'p_fw': fw[:32] if isinstance(fw, str) else None,
        'p_rssi': rssi,
        'p_snooze_s': snz,
        'p_siren': bool(b.get('siren')),
        'p_fw_status': fws[:120] if isinstance(fws, str) else None,
    }


def do_sync(raw):
    try:
        params = build_rpc(json.loads(raw))
    except (ValueError, TypeError) as e:
        return 400, {'err': str(e)}
    try:
        code, body = _upstream('POST', '/rest/v1/rpc/node_sync', json.dumps(params).encode())
    except Exception as e:
        _state['last_err'] = '%s %s' % (time.strftime('%H:%M:%S'), e)
        log('supabase unreachable:', e)
        return 502, {'err': 'upstream'}
    if code == 200:
        _state['last_ok'] = time.time()
        try:
            return 200, json.loads(body)
        except ValueError:
            return 502, {'err': 'bad upstream reply'}
    try:
        info = json.loads(body)
    except ValueError:
        info = {}
    if info.get('code') == '28000':
        log('rejected device', params['p_device'], '(wrong key or not provisioned)')
        return 401, {'err': 'unauthorised'}
    _state['last_err'] = '%s http %s %s' % (time.strftime('%H:%M:%S'), code, info.get('message'))
    log('supabase error', code, body[:300])
    return 502, {'err': 'upstream %s' % code}


# ── /fw ──────────────────────────────────────────────────────────────────
def _storage_get(path):
    code, body = _upstream('GET', '/storage/v1/object/public/firmware/' + path, timeout=15)
    if code != 200:
        raise IOError('storage %s -> http %s' % (path, code))
    return body


def _write_atomic(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def manifest(ver):
    d = os.path.join(CACHE, ver)
    p = os.path.join(d, 'manifest.json')
    if os.path.exists(p):
        with open(p, 'rb') as f:
            raw = f.read()
        return raw, json.loads(raw)
    raw = _storage_get(ver + '/manifest.json')
    man = json.loads(raw)
    if not isinstance(man, dict) or man.get('version') != ver or not isinstance(man.get('files'), list):
        raise IOError('manifest for %s is invalid' % ver)
    for f in man['files']:
        if not RE_FILE.match(str(f.get('n'))) or len(str(f.get('h'))) != 64 or not isinstance(f.get('s'), int):
            raise IOError('manifest entry invalid: %r' % f)
    os.makedirs(d, exist_ok=True)
    _write_atomic(p, raw)
    return raw, man


def fw_file(ver, name):
    with _fw_lock:
        raw, man = manifest(ver)
        if name == 'manifest.json':
            return raw
        entry = next((f for f in man['files'] if f['n'] == name), None)
        if entry is None:
            raise FileNotFoundError(name)
        p = os.path.join(CACHE, ver, name)
        if os.path.exists(p):
            with open(p, 'rb') as f:
                data = f.read()
            if hashlib.sha256(data).hexdigest() == entry['h']:
                return data
        data = _storage_get('%s/%s' % (ver, name))
        if len(data) != entry['s'] or hashlib.sha256(data).hexdigest() != entry['h']:
            raise IOError('%s/%s does not match its manifest' % (ver, name))
        _write_atomic(p, data)
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = 'tms-gateway/' + VERSION
    timeout = 10

    def log_message(self, fmt, *args):
        pass  # one line per request every few seconds is noise; errors are logged

    def _send(self, code, payload, ctype='application/json'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/sync':
            return self._send(404, {'err': 'not found'})
        n = int(self.headers.get('Content-Length') or 0)
        if not 0 < n <= 2048:
            return self._send(400, {'err': 'bad length'})
        code, payload = do_sync(self.rfile.read(n))
        self._send(code, payload)

    def do_GET(self):
        if self.path == '/health':
            lo = _state['last_ok']
            return self._send(200, {'ok': True, 'gateway': VERSION,
                                    'supabase_ok_s_ago': None if lo is None else int(time.time() - lo),
                                    'last_error': _state['last_err']})
        parts = self.path.split('/')
        if len(parts) == 4 and parts[1] == 'fw' and RE_VER.match(parts[2]) \
                and (parts[3] == 'manifest.json' or RE_FILE.match(parts[3])):
            try:
                return self._send(200, fw_file(parts[2], parts[3]), 'application/octet-stream')
            except FileNotFoundError:
                return self._send(404, {'err': 'no such file'})
            except Exception as e:
                log('firmware error:', e)
                return self._send(502, {'err': 'firmware unavailable'})
        self._send(404, {'err': 'not found'})


def main():
    if not SUPABASE_URL or not ANON_KEY:
        sys.exit('SUPABASE_URL and SUPABASE_ANON_KEY must be set')
    os.makedirs(CACHE, exist_ok=True)
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    srv.daemon_threads = True
    log('tms-gateway %s listening on :%d -> %s' % (VERSION, PORT, SUPABASE_URL))
    srv.serve_forever()


if __name__ == '__main__':
    main()
