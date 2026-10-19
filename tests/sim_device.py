"""Runs the real firmware files on CPython with simulated hardware (tests only).
Start with cwd = the simulated board's filesystem. Exit code 99 = machine.reset()."""
import os
import sys
import threading
import time

SHIMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shims')
sys.path[:0] = [SHIMS, os.getcwd()]

PERIOD = 1 << 30
HALF, MASK = PERIOD >> 1, PERIOD - 1
OFFSET = int(os.environ.get('SIM_TICKS_START', '0'))
_base = time.monotonic()
time.ticks_ms = lambda: (OFFSET + int((time.monotonic() - _base) * 1000)) & MASK
time.ticks_diff = lambda a, b: ((a - b + HALF) & MASK) - HALF
time.ticks_add = lambda a, d: (a + d) & MASK
time.sleep_ms = lambda m: time.sleep(m / 1000.0)
time.sleep_us = lambda u: None

import _ctl      # noqa: E402
import _status   # noqa: E402
import config    # noqa: E402
import disp      # noqa: E402

_raw = disp.TM1637.raw


def _rec(self, segs):
    _status.DISPLAY[0] = _status.seg_text(list(segs))
    return _raw(self, segs)


disp.TM1637.raw = _rec


def _press(ms):
    p = _ctl.PINS.get(config.PIN_BUTTON)
    if p is None or p.handler is None:
        return
    for v in (0, 1, 0, 1, 0):          # contact bounce on press
        p._v = v
        p.handler(p)
        time.sleep(0.002)
    time.sleep(ms / 1000.0)
    for v in (1, 0, 1, 0, 1):          # and on release
        p._v = v
        p.handler(p)
        time.sleep(0.002)


def _watch():
    done = set()
    while True:
        try:
            for b in _ctl.ctl().get('press', []):
                if b['id'] not in done:
                    done.add(b['id'])
                    _press(b['ms'])
            _status.write()
        except Exception as e:
            print('sim watch error', e, file=sys.stderr)
        time.sleep(0.1)


threading.Thread(target=_watch, daemon=True).start()

for script in ('boot.py', 'main.py'):
    g = {'__name__': '__main__'}
    exec(compile(open(script).read(), script, 'exec'), g)
