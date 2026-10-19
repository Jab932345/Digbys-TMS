"""Minimal stand-in for Supabase's REST RPC + public Storage (tests only)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2
import psycopg2.extras

DSN = 'host=/tmp port=55432 user=postgres dbname=tms'
STORE = os.environ['STORAGE_DIR']
ANON = os.environ['ANON_KEY']
ARGS = ['p_device', 'p_key', 'p_temp', 'p_fw', 'p_rssi', 'p_snooze_s', 'p_siren', 'p_fw_status']


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get('apikey') != ANON or self.headers.get('Authorization') != 'Bearer ' + ANON:
            return self._send(401, {'message': 'no api key'})
        if self.path != '/rest/v1/rpc/node_sync':
            return self._send(404, {'message': 'nope'})
        args = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if set(args) != set(ARGS):
            return self._send(400, {'message': 'bad args %s' % sorted(args)})
        c = psycopg2.connect(DSN)
        c.autocommit = True
        try:
            cur = c.cursor()
            cur.execute('set role anon')
            cur.execute('select node_sync(%s)' % ','.join('%%(%s)s' % a for a in ARGS), args)
            self._send(200, cur.fetchone()[0])
        except psycopg2.Error as e:
            self._send(403 if e.pgcode == '28000' else 400, {'code': e.pgcode, 'message': str(e).strip()})
        finally:
            c.close()

    def do_GET(self):
        pre = '/storage/v1/object/public/firmware/'
        if not self.path.startswith(pre):
            return self._send(404, {'message': 'nope'})
        p = os.path.join(STORE, self.path[len(pre):])
        if not os.path.isfile(p):
            return self._send(400, {'statusCode': '404', 'error': 'not_found'})
        self._send(200, open(p, 'rb').read(), 'application/octet-stream')


ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
