# Simulated machine module for running the firmware under CPython (tests only).
import os, threading, _ctl


class Pin:
    IN, OUT, PULL_UP = 0, 1, 2
    IRQ_FALLING, IRQ_RISING = 4, 8

    def __init__(self, n, mode=None, pull=None, value=None):
        self.n = n
        self._v = 1 if pull == Pin.PULL_UP else 0
        if value is not None:
            self._v = value
        self.handler = None
        _ctl.PINS[n] = self

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(v)

    def __call__(self, v=None):
        return self.value(v)

    def init(self, *a, **k):
        if 'value' in k:
            self._v = k['value']

    def irq(self, handler=None, trigger=None):
        self.handler = handler


class Timer:
    ONE_SHOT, PERIODIC = 0, 1

    def __init__(self, i=-1):
        self.t = None

    def init(self, period=0, mode=0, callback=None):
        self.t = threading.Timer(period / 1000.0, callback, args=(self,))
        self.t.daemon = True
        self.t.start()


def reset():
    import _status
    _status.write(final=True)
    os._exit(99)
