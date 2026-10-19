# DS18B20 probe, read without blocking the main loop.
# Call start() to begin a conversion, then collect() at least 800 ms later.
import time
import onewire
import ds18x20
from machine import Pin


class Probe:
    def __init__(self, pin):
        self.ds = ds18x20.DS18X20(onewire.OneWire(Pin(pin)))
        self.roms = []
        self.fails = 0
        self.temp = None          # last good reading, None = fault
        self.started = None
        self.has_result = False   # True once a reading or a confirmed fault exists

    def _scan(self):
        try:
            self.roms = self.ds.scan()
        except Exception:
            self.roms = []

    def start(self):
        if not self.roms:
            self._scan()
        if not self.roms:
            self._fail()
            return
        try:
            self.ds.convert_temp()
            self.started = time.ticks_ms()
        except Exception:
            self.roms = []
            self._fail()

    def ready(self):
        return self.started is not None and time.ticks_diff(time.ticks_ms(), self.started) >= 800

    def collect(self):
        self.started = None
        try:
            t = self.ds.read_temp(self.roms[0])
        except Exception:
            self.roms = []
            self._fail()
            return
        # 85.0 is the sensor's power-on value, not a reading; anything outside the
        # DS18B20's range is a wiring fault.
        if t is None or t == 85.0 or t < -55 or t > 125:
            self._fail()
            return
        self.has_result = True
        self.fails = 0
        self.temp = t

    def _fail(self):
        # Three failures in a row (about 15 s) before declaring a probe fault,
        # so one noisy read never trips the alarm.
        self.fails += 1
        if self.fails >= 3:
            self.temp = None
            self.has_result = True
