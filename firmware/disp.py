# TM1637 4-digit display: minimal bit-banged driver plus text helpers.
# Protocol timing follows the widely used mcauser micropython-tm1637 driver.
import time
from machine import Pin

_SEG = {
    '0': 0x3F, '1': 0x06, '2': 0x5B, '3': 0x4F, '4': 0x66, '5': 0x6D,
    '6': 0x7D, '7': 0x07, '8': 0x7F, '9': 0x6F, ' ': 0x00, '-': 0x40,
    'A': 0x77, 'b': 0x7C, 'C': 0x39, 'c': 0x58, 'd': 0x5E, 'E': 0x79,
    'F': 0x71, 'h': 0x74, 'H': 0x76, 'i': 0x10, 'L': 0x38, 'n': 0x54,
    'o': 0x5C, 'P': 0x73, 'r': 0x50, 'S': 0x6D, 't': 0x78, 'U': 0x3E,
    'u': 0x1C, 'y': 0x6E, '_': 0x08, '*': 0x63,  # '*' = degree sign
}
_D = 10  # microseconds


class TM1637:
    def __init__(self, clk, dio, brightness=4):
        self.clk = Pin(clk, Pin.OUT, value=0)
        self.dio = Pin(dio, Pin.OUT, value=0)
        self.bright = brightness & 7
        self._last = None
        time.sleep_us(_D)
        self._cmd(0x40)
        self._ctrl()

    def _start(self):
        self.dio(0)
        time.sleep_us(_D)
        self.clk(0)
        time.sleep_us(_D)

    def _stop(self):
        self.dio(0)
        time.sleep_us(_D)
        self.clk(1)
        time.sleep_us(_D)
        self.dio(1)

    def _byte(self, b):
        for i in range(8):
            self.dio((b >> i) & 1)
            time.sleep_us(_D)
            self.clk(1)
            time.sleep_us(_D)
            self.clk(0)
            time.sleep_us(_D)
        self.clk(0)
        time.sleep_us(_D)
        self.clk(1)
        time.sleep_us(_D)
        self.clk(0)
        time.sleep_us(_D)

    def _cmd(self, c):
        self._start()
        self._byte(c)
        self._stop()

    def _ctrl(self):
        self._cmd(0x80 | 0x08 | self.bright)

    def raw(self, segs):
        segs = bytes(segs)
        if segs == self._last:
            return
        self._last = segs
        self._cmd(0x40)
        self._start()
        self._byte(0xC0)
        for s in segs:
            self._byte(s)
        self._stop()
        self._ctrl()

    def text(self, s):
        # Up to 4 visible characters; a '.' lights the previous digit's point.
        out = []
        for ch in s:
            if ch == '.' and out:
                out[-1] |= 0x80
            else:
                out.append(_SEG.get(ch, 0x00))
        out = (out + [0, 0, 0, 0])[:4]
        self.raw(out)


def fmt_temp(t, has_dp):
    # 4-character rendering of a temperature. None -> 'Err '.
    if t is None:
        return 'Err '
    if has_dp:
        v = round(t * 10) / 10
        if -99.9 <= v <= 99.9:
            s = '%.1f' % v              # e.g. '3.2', '-4.5', '12.0'
            if s == '-0.0':
                s = '0.0'
            return ('    ' + s)[-5:]     # 4 digits + the point
    v = int(round(t))
    if -99 <= v <= 99:
        s = '%d*' % v                    # e.g. '3*', '-18*'
        if len(s) < 3:
            s = s + 'C'                  # ' 3*C'
        return ('    ' + s)[-4:]
    return ('    %d' % v)[-4:]


def fmt_snooze(secs):
    m = (secs + 59) // 60
    if m <= 99:
        return 'Sn%2d' % m
    return 'Sn%dh' % min(9, (m + 59) // 60)
