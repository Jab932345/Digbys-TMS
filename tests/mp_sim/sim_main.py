# Runs the real firmware on a unix MicroPython built with the ESP8266's
# language limits (tests only). cwd = the simulated board's filesystem.
# Exit code 99 = machine.reset().
import sys
import os
import time
import _thread
import json

HERE = os.getenv('SIM_SHIMS')
sys.path.insert(0, HERE)
sys.path.insert(1, '')

import sim_machine                     # noqa: E402
sys.modules['machine'] = sim_machine   # the unix port has a built-in machine module
import sim_ctl                         # noqa: E402
import config                          # noqa: E402
import disp                            # noqa: E402

_raw = disp.TM1637.raw


def _rec(self, segs):
    sim_ctl.DISPLAY[0] = sim_ctl.seg_text(segs)
    return _raw(self, segs)


disp.TM1637.raw = _rec


def _press(ms):
    p = sim_ctl.PINS.get(config.PIN_BUTTON)
    if p is None or p.handler is None:
        return
    for v in (0, 1, 0, 1, 0):
        p._v = v
        p.handler(p)
        time.sleep_ms(2)
    time.sleep_ms(ms)
    for v in (1, 0, 1, 0, 1):
        p._v = v
        p.handler(p)
        time.sleep_ms(2)


def _watch():
    done = []
    while True:
        try:
            for b in sim_ctl.ctl().get('press', []):
                if b['id'] not in done:
                    done.append(b['id'])
                    _press(b['ms'])
            sim_ctl.write_status()
        except Exception as e:
            print('sim watch error', repr(e))
        time.sleep_ms(100)


_thread.start_new_thread(_watch, ())

for script in ('boot.py', 'main.py'):
    with open(script) as f:
        src = f.read()
    exec(src, {'__name__': '__main__'})
