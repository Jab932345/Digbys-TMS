# Two-way sync with the website through the Pi gateway, and OTA hand-off.
# Split out of node.py to keep each module small enough to compile on an ESP8266.
import time
import gc

import config
import net
import ota


def _now():
    return time.ticks_ms()


def _left(deadline, now):
    # ms until a ticks deadline (<= 0 when passed). None deadline -> 0.
    return 0 if deadline is None else time.ticks_diff(deadline, now)


def sync(u, now):
    snz = None
    seq = u.pend_seq
    if u.pending == 0:
        snz = 0
    elif u.pending:
        left = _left(u.snz_until, now) // 1000
        if left > 0:
            snz = left
        else:
            u.pending = None   # local snooze already ran out; nothing to send
    body = {'d': config.DEVICE_ID, 'k': config.DEVICE_KEY, 't': u.probe.temp,
            'fw': u.fw, 'rssi': net.rssi(), 'snz': snz, 'siren': u.siren}
    if u.fw_status:
        body['fws'] = u.fw_status
    try:
        code, res = net.post_json('/sync', body)
    except Exception:
        # Gateway unreachable: back off to 30 s so the display and button stay lively.
        u.fails = min(u.fails + 1, 6)
        u.next_sync = time.ticks_add(_now(), 5000 * u.fails)
        return
    u.fails = 0
    now = _now()
    if code == 401:
        u.auth_bad = True
        u.next_sync = time.ticks_add(now, 30000)
        return
    if code != 200 or not isinstance(res, dict):
        u.next_sync = time.ticks_add(now, 10000)
        return
    u.auth_bad = False
    u.last_ok = now
    u.fw_status = None
    if seq == u.pend_seq:
        u.pending = None
    # The server's answer is the truth, unless a newer press is waiting to go up.
    if u.pending is None:
        s = res.get('snooze_s') or 0
        u.snz_until = time.ticks_add(now, s * 1000) if s > 0 else None
    d = res.get('defrost_s') or 0
    u.def_until = time.ticks_add(now, d * 1000) if res.get('defrost') and d > 0 else None
    try:
        w, c, dl = float(res['warn']), float(res['crit']), int(res['delay_s'])
        if (w, c, dl) != (u.warn, u.crit, u.delay) and w < c:
            u.warn, u.crit, u.delay = w, c, dl
            u._save_cache()
    except Exception:
        pass
    try:
        u.interval = max(2, min(60, int(res.get('interval_s') or 5)))
    except Exception:
        u.interval = 5
    u.next_sync = time.ticks_add(now, u.interval * 1000)
    if ota.in_trial():
        ota.confirm()
        u.fw_status = ota.last_event()      # tell the website on the next sync
    maybe_update(u, res.get('fw_target'), now)

def maybe_update(u, target, now):
    if not target or target == u.fw or target in ota.bad_versions():
        return
    if u.level() == 'critical' or u.siren:
        return                                   # never update mid-alarm
    n = ota.tries(target)
    if n >= ota.MAX_TRIES:
        return                                   # gave up; publish a new version number
    if n and not u.uptime_over(600000):
        return                                   # 10 minutes between attempts
    u.updating = True
    u.show(now)
    ota.request(target)                          # restarts; boot.py installs it
