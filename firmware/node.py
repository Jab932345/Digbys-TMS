# Digby's TMS unit: probe, display, siren, button, and two-way sync with the
# website through the Pi gateway. The alarm works without any network.
import os
import time
import gc
from machine import Pin

try:
    import ujson as json
except ImportError:
    import json

import config
import net
import ota
import link
from disp import TM1637, fmt_temp, fmt_snooze
from probe import Probe

from link import _now, _left

LONG_MS = 2500
READ_MS = 2000


class Button:
    # Edges are caught by interrupt, so a press during a network call is not lost.
    def __init__(self, pin):
        self.p = Pin(pin, Pin.IN, Pin.PULL_UP)
        self.down = False
        self.t0 = 0
        self.lock = None           # ticks until which edges are ignored (debounce)
        self.event = None
        self.p.irq(handler=self._irq, trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING)

    def _irq(self, p):
        self._edge(p.value(), time.ticks_ms())

    def _edge(self, v, t):
        # Runs from the interrupt and from the main loop, so a press is caught
        # even if one of the two misses it. A press must last 50 ms to count.
        if v == 0:
            if not self.down and self.lock is None:
                self.down = True
                self.t0 = t
        elif self.down:
            self.down = False
            d = time.ticks_diff(t, self.t0)
            if d >= 50:
                self.event = 'long' if d >= LONG_MS else 'short'
                self.lock = time.ticks_add(t, 150)

    def take(self, now):
        # Expire the lockout here, never by comparing old ticks (they wrap every ~12 days).
        if self.lock is not None and not -1 < time.ticks_diff(self.lock, now) <= 150:
            self.lock = None
        self._edge(self.p.value(), time.ticks_ms())
        e = self.event
        self.event = None
        return e


class Unit:
    def __init__(self):
        on = 0 if config.RELAY_ACTIVE_LOW else 1
        self.relay_on, self.relay_off = on, 1 - on
        self.relay = Pin(config.PIN_RELAY, Pin.OUT, value=self.relay_off)
        self.disp = TM1637(config.PIN_TM_CLK, config.PIN_TM_DIO, config.BRIGHTNESS)
        self.disp.text('----')
        self.probe = Probe(config.PIN_PROBE)
        self.btn = Button(config.PIN_BUTTON)
        self.warn = config.FALLBACK_WARN
        self.crit = config.FALLBACK_CRIT
        self.delay = config.FALLBACK_DELAY_S
        self._load_cache()
        self.interval = 5
        self.snz_until = None      # ticks deadline, None = not snoozed
        self.def_until = None      # ticks deadline, None = defrost off
        self.pending = None        # button action not yet confirmed by the server
        self.pend_seq = 0
        self.crit_since = None
        self.crit_due = False      # latched once critical has lasted the alarm delay
        self.siren = False
        self.last_ok = None
        self.fails = 0             # failed syncs in a row (for back-off)
        self.next_sync = _now()
        self.next_read = _now()
        self.fw = ota.version()
        self.fw_status = ota.last_event()
        try:
            with open('crash.txt') as f:
                c = 'crash ' + f.read().strip()
            os.remove('crash.txt')
            self.fw_status = (c + ' | ' + self.fw_status) if self.fw_status else c
            self.fw_status = self.fw_status[:120]
        except Exception:
            pass
        self.started = _now()
        self.up_long = False       # set once uptime passes 10 min (wrap-safe)
        self.updating = False
        self.auth_bad = False

    def uptime_over(self, ms):
        if not self.up_long and time.ticks_diff(_now(), self.started) >= ms:
            self.up_long = True
        return self.up_long

    # ── settings cache (thresholds survive a restart with no network) ──────
    def _load_cache(self):
        try:
            with open('cache.json') as f:
                c = json.load(f)
            self.warn, self.crit, self.delay = float(c['w']), float(c['c']), int(c['d'])
        except Exception:
            pass

    def _save_cache(self):
        try:
            with open('cache.json', 'w') as f:
                json.dump({'w': self.warn, 'c': self.crit, 'd': self.delay}, f)
        except Exception:
            pass

    # ── siren ──────────────────────────────────────────────────────────────
    def _set_relay(self, on):
        self.relay.value(self.relay_on if on else self.relay_off)

    def chirp(self, n):
        for i in range(n):
            self._set_relay(True)
            time.sleep_ms(120)
            self._set_relay(False)
            if i < n - 1:
                time.sleep_ms(150)

    # ── button ─────────────────────────────────────────────────────────────
    def handle_button(self, now):
        e = self.btn.take(now)
        if e is None:
            return
        self.pend_seq += 1
        if e == 'short':
            self.snz_until = time.ticks_add(now, config.SNOOZE_MIN * 60000)
            self.pending = config.SNOOZE_MIN * 60   # seconds; recomputed when sent
            self.siren = False
            self._set_relay(False)
            self.chirp(1)
        else:
            self.snz_until = None
            self.pending = 0
            self.chirp(2)
        self.next_sync = now                        # tell the website straight away

    # ── alarm ──────────────────────────────────────────────────────────────
    def level(self):
        t = self.probe.temp
        if t is None or t >= self.crit:
            return 'critical'
        if t >= self.warn:
            return 'warning'
        return 'ok'

    def evaluate(self, now):
        if _left(self.snz_until, now) <= 0:
            self.snz_until = None
        if _left(self.def_until, now) <= 0:
            self.def_until = None
        if self.level() == 'critical':
            if self.crit_since is None:
                self.crit_since = now
            if not self.crit_due and time.ticks_diff(now, self.crit_since) >= self.delay * 1000:
                self.crit_due = True
            self.siren = self.crit_due and self.snz_until is None and self.def_until is None
        else:
            self.crit_since = None
            self.crit_due = False
            self.siren = False
        self._set_relay(self.siren)

    # ── display ────────────────────────────────────────────────────────────
    def show(self, now):
        t = fmt_temp(self.probe.temp, config.DISPLAY_HAS_DP)
        if self.siren:
            self.disp.text(t if (now // 500) % 2 else '    ')
            return
        frames = [t]
        if self.updating:
            frames = ['UPd ']
        if self.snz_until is not None:
            frames.append(fmt_snooze(_left(self.snz_until, now) // 1000))
        if self.def_until is not None:
            frames.append('dEF ')
        if self.last_ok is not None and not 0 <= time.ticks_diff(now, self.last_ok) <= 30000:
            self.last_ok = None                 # stale (also safe across tick wrap)
        if self.auth_bad:
            frames.append('AUth')
        elif self.last_ok is None:
            frames.append('nEt ')
        self.disp.text(frames[(now // 2000) % len(frames)])

    # ── main loop ──────────────────────────────────────────────────────────
    def run(self):
        net.setup()
        while True:
            now = _now()
            self.uptime_over(600000)
            # Deadlines further ahead than they could ever be set are stale: run now.
            if time.ticks_diff(self.next_read, now) > READ_MS:
                self.next_read = now
            if time.ticks_diff(self.next_sync, now) > 61000:
                self.next_sync = now
            self.handle_button(now)
            if self.probe.started is None and time.ticks_diff(now, self.next_read) >= 0:
                self.probe.start()
                self.next_read = time.ticks_add(now, READ_MS)
            elif self.probe.ready():
                self.probe.collect()
            if self.probe.has_result:
                self.evaluate(now)
            self.show(now)
            if net.tend() and self.probe.has_result and time.ticks_diff(now, self.next_sync) >= 0:
                link.sync(self, now)
                gc.collect()
            time.sleep_ms(40)


def run():
    Unit().run()
