# Wi-Fi and a tiny plain-HTTP client for talking to the TMS gateway on the Pi.
# Plain sockets with hard timeouts: connecting gives up after 3 s, a request after 6 s.
import time
import socket
import network

try:
    import ujson as json
except ImportError:
    import json

import config

_sta = network.WLAN(network.STA_IF)
_last_try = None
_last_cfg = None
_idx = 0


def setup():
    try:
        network.WLAN(network.AP_IF).active(False)
    except Exception:
        pass
    _sta.active(True)
    connect(True)


def connect(new_creds=False):
    # Starts a connection attempt. Never waits.
    # Passing an SSID/password makes the ESP8266 save them to flash, so that is
    # only done at boot or when switching networks (at most every 2 minutes);
    # ordinary retries reuse the saved credentials and write nothing.
    global _last_try, _last_cfg, _idx
    nets = config.WIFI
    _last_try = time.ticks_ms()
    try:
        _sta.disconnect()
    except Exception:
        pass
    try:
        if new_creds and nets:
            ssid, pw = nets[_idx % len(nets)]
            _idx += 1
            _sta.connect(ssid, pw)
            _last_cfg = _last_try
        else:
            _sta.connect()
    except Exception:
        pass


def up():
    return _sta.isconnected()


def _since(t):
    # ms since a ticks stamp; very old or wrapped stamps count as "long ago".
    if t is None:
        return 1 << 28
    d = time.ticks_diff(time.ticks_ms(), t)
    return d if d >= 0 else 1 << 28


def tend():
    # Call every loop. Retries a dropped connection every 20 s; with more than
    # one network configured, moves to the next one every 2 minutes.
    if _sta.isconnected():
        return True
    if _since(_last_try) > 20000:
        connect(len(config.WIFI) > 1 and _since(_last_cfg) > 120000)
    return False


def rssi():
    try:
        return _sta.status('rssi')
    except Exception:
        return None


def _host_port():
    # config.GATEWAY like "http://192.168.8.20:8787"
    g = config.GATEWAY
    if g.startswith('http://'):
        g = g[7:]
    g = g.rstrip('/')
    if ':' in g:
        h, p = g.split(':', 1)
        return h, int(p)
    return g, 80


def _open(method, path, body=None, timeout=4):
    host, port = _host_port()
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    try:
        s.settimeout(3)                 # the Pi is on the same network: 3 s is plenty
        s.connect(addr)
        s.settimeout(timeout)
        hdr = '%s %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n' % (method, path, host)
        if body is not None:
            hdr += 'Content-Type: application/json\r\nContent-Length: %d\r\n' % len(body)
        s.sendall((hdr + '\r\n').encode())
        if body is not None:
            s.sendall(body)
        return s, _Reader(s)
    except Exception:
        s.close()
        raise


class _Reader:
    # Buffered reader built on recv(), works on MicroPython and CPython sockets.
    def __init__(self, s):
        self.s = s
        self.buf = b''

    def _fill(self):
        d = self.s.recv(512)
        if not d:
            return False
        self.buf += d
        return True

    def line(self):
        while b'\n' not in self.buf:
            if len(self.buf) > 1024 or not self._fill():
                raise OSError('bad header')
        i = self.buf.index(b'\n')
        ln, self.buf = self.buf[:i + 1], self.buf[i + 1:]
        return ln

    def read(self, n):
        while len(self.buf) < n:
            if not self._fill():
                break
        d, self.buf = self.buf[:n], self.buf[n:]
        return d

    def headers(self):
        status = self.line().split()
        code = int(status[1].decode())
        length = -1
        while True:
            ln = self.line().strip()
            if not ln:
                break
            i = ln.find(b':')                 # no str.partition on ESP8266 builds
            if i > 0 and ln[:i].strip().lower() == b'content-length':
                length = int(ln[i + 1:].strip().decode())
        return code, length


def _recv_into(s, mv):
    # CPython sockets (tests) have recv_into rather than readinto.
    return s.recv_into(mv)


def post_json(path, obj, timeout=6):
    # Returns (status_code, parsed_json_or_None). Raises OSError on network failure.
    body = json.dumps(obj).encode()
    s, r = _open('POST', path, body, timeout)
    try:
        code, length = r.headers()
        if length < 0 or length > 4096:
            raise OSError('bad length')
        data = r.read(length)
        if len(data) != length:
            raise OSError('short body')
    finally:
        s.close()
    try:
        return code, json.loads(data)
    except ValueError:
        return code, None


def get_to_file(path, fname, hasher=None, max_len=60000, timeout=6):
    # Streams a GET into fname. Returns bytes written. Raises on any problem.
    s, r = _open('GET', path, None, timeout)
    try:
        code, length = r.headers()
        if code != 200:
            raise OSError('http %d' % code)
        if length < 0 or length > max_len:
            raise OSError('bad length')
        got = 0
        buf = bytearray(512)              # one buffer reused: no heap churn
        mv = memoryview(buf)
        with open(fname, 'wb') as f:
            if r.buf:                     # body bytes that arrived with the headers
                chunk = r.buf[:length]
                r.buf = b''
                f.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
                got = len(chunk)
            while got < length:
                n = s.readinto(mv[:min(512, length - got)]) if hasattr(s, 'readinto') \
                    else _recv_into(s, mv[:min(512, length - got)])
                if not n:
                    raise OSError('short body')
                f.write(mv[:n])
                if hasher is not None:
                    hasher.update(mv[:n])
                got += n
        return got
    finally:
        s.close()


def get_json(path, timeout=4):
    s, r = _open('GET', path, None, timeout)
    try:
        code, length = r.headers()
        if code != 200:
            raise OSError('http %d' % code)
        if length < 0 or length > 4096:
            raise OSError('bad length')
        data = r.read(length)
        if len(data) != length:
            raise OSError('short body')
        return json.loads(data)
    finally:
        s.close()
