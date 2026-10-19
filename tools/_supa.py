"""Shared helper for the Mac-side tools: Supabase REST calls with the service key.

The service key is never stored in the repo. Export it in the terminal first:
    export SUPABASE_SERVICE_KEY='...'   (Supabase -> Project Settings -> API -> service_role)
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get('SUPABASE_URL', 'https://jaajrllkozknilvmdezt.supabase.co').rstrip('/')


def key():
    k = os.environ.get('SUPABASE_SERVICE_KEY', '').strip()
    if not k:
        sys.exit('Set SUPABASE_SERVICE_KEY first (Supabase > Project Settings > API > service_role).')
    return k


def call(method, path, body=None, headers=None, raw=False):
    k = key()
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header('apikey', k)
    req.add_header('Authorization', 'Bearer ' + k)
    if body is not None and not isinstance(body, bytes):
        req.add_header('Content-Type', 'application/json')
    for h, v in (headers or {}).items():
        req.add_header(h, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        sys.exit('Supabase %s %s failed: HTTP %s %s' % (method, path, e.code, e.read()[:400].decode(errors='replace')))
    if raw:
        return out
    return json.loads(out) if out else None


def q(v):
    return urllib.parse.quote(str(v), safe='')
