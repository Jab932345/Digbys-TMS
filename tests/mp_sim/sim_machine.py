# Simulated machine module for MicroPython unix (tests only).
import sys
import time
import _thread
import sim_ctl


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    def __init__(self, n, mode=None, pull=None, value=None):
        self.n = n
        self._v = 1 if pull == Pin.PULL_UP else 0
        if value is not None:
            self._v = value
        self.handler = None
        sim_ctl.PINS[n] = self

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(v)

    def __call__(self, v=None):
        return self.value(v)

    def irq(self, handler=None, trigger=None):
        self.handler = handler


class Timer:
    ONE_SHOT = 0
    PERIODIC = 1

    def __init__(self, i=-1):
        pass

    def init(self, period=0, mode=0, callback=None):
        def run():
            time.sleep_ms(period)
            callback(self)
        _thread.start_new_thread(run, ())


def reset():
    sim_ctl.write_status(True)
    sys.exit(99)
