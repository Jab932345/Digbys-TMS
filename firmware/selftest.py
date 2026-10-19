# Hardware and network check for one unit. Run from the Mac with:
#   ampy --port /dev/tty.usbserial-XXXX run firmware/selftest.py
# It prints PASS/FAIL for each part. Nothing here changes the unit's settings.
import time
import config


def say(ok, what, detail=''):
    print('%s  %s %s' % ('PASS' if ok else 'FAIL', what, detail))


print('\n== Digby TMS self-test:', config.DEVICE_ID, '==')

# 1. Siren relay off first.
from machine import Pin
off = 1 if config.RELAY_ACTIVE_LOW else 0
relay = Pin(config.PIN_RELAY, Pin.OUT, value=off)

# 2. Display
try:
    from disp import TM1637
    d = TM1637(config.PIN_TM_CLK, config.PIN_TM_DIO, config.BRIGHTNESS)
    d.text('8.8.8.8.')
    print('LOOK  display shows 8888 for 4 s. Dots visible? -> set DISPLAY_HAS_DP = True')
    time.sleep(4)
    d.text('-12.5' if config.DISPLAY_HAS_DP else '-12*')
    print('LOOK  display now shows', '-12.5' if config.DISPLAY_HAS_DP else '-12 degrees')
    time.sleep(2)
    say(True, 'display driven (check by eye)')
except Exception as e:
    d = None
    say(False, 'display', repr(e))

# 3. Probe
probe_t = None
try:
    import onewire
    import ds18x20
    ds = ds18x20.DS18X20(onewire.OneWire(Pin(config.PIN_PROBE)))
    roms = []
    for _ in range(10):                 # some probes need a few scans after power-up
        roms = ds.scan()
        if roms:
            break
        time.sleep_ms(750)
    say(len(roms) == 1, 'probe found', '%d sensor(s) on GPIO%d' % (len(roms), config.PIN_PROBE))
    for i in range(3):
        ds.convert_temp()
        time.sleep_ms(800)
        t = ds.read_temp(roms[0])
        print('      reading %d: %.2f C' % (i + 1, t))
        if d:
            from disp import fmt_temp
            d.text(fmt_temp(t, config.DISPLAY_HAS_DP))
    say(t != 85.0 and -55 < t < 125, 'probe reading sane')
    if t != 85.0 and -55 < t < 125:
        probe_t = t
except Exception as e:
    say(False, 'probe', repr(e))

# 4. Siren
print('LISTEN siren chirps twice now')
for _ in range(2):
    relay.value(1 - off)
    time.sleep_ms(150)
    relay.value(off)
    time.sleep_ms(300)
say(relay.value() == off, 'siren relay left OFF')

# 5. Button
btn = Pin(config.PIN_BUTTON, Pin.IN, Pin.PULL_UP)
print('PRESS the button within 15 s')
pressed = False
t0 = time.ticks_ms()
n = 0
while time.ticks_diff(time.ticks_ms(), t0) < 15000:
    if btn.value() == 0:
        pressed = True
        break
    time.sleep_ms(20)
    n += 1
    if n % 50 == 0:
        print('      waiting for button...')   # keeps ampy from timing out
say(pressed, 'button', '(GPIO%d)' % config.PIN_BUTTON)

# 6. Wi-Fi
import network
sta = network.WLAN(network.STA_IF)
sta.active(True)
if not sta.isconnected():
    for ssid, pw in config.WIFI:
        sta.connect(ssid, pw)
        t0 = time.ticks_ms()
        while not sta.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
            time.sleep_ms(1000)
            print('      connecting to', ssid, '...')
        if sta.isconnected():
            break
say(sta.isconnected(), 'wifi', str(sta.ifconfig()[0]) + ' rssi %s' % sta.status('rssi') if sta.isconnected() else '')

# 7. Gateway + website
if sta.isconnected():
    import net
    try:
        h = net.get_json('/health')
        say(bool(h.get('ok')), 'gateway reachable', config.GATEWAY + ' ' + str(h))
    except Exception as e:
        say(False, 'gateway reachable', config.GATEWAY + ' ' + repr(e))
    if probe_t is None:
        say(False, 'website sync', 'skipped: fix the probe first (a real reading is logged)')
    else:
        try:
            import ota
            code, res = net.post_json('/sync', {'d': config.DEVICE_ID, 'k': config.DEVICE_KEY,
                                                't': probe_t, 'fw': ota.version(), 'siren': False})
            say(code == 200, 'website sync', 'http %s %s' % (code, res))
            if code == 200:
                print('      unit name on website:', res.get('name'),
                      '| warn', res.get('warn'), '| crit', res.get('crit'))
        except Exception as e:
            say(False, 'website sync', repr(e))

# 8. The app itself fits in memory (each module is compiled on the board).
import gc
import sys
print('      MicroPython', '.'.join(str(x) for x in sys.implementation.version[:3]))
say(sys.implementation.version[:3] == (1, 29, 0), 'MicroPython 1.29.0 (the .mpy files are built for it)')
gc.collect()
before = gc.mem_free()
try:
    import link
    import probe
    import node
    gc.collect()
    after = gc.mem_free()
    say(after > 8000, 'app loads', '(%d bytes free after loading, %d before)' % (after, before))
except MemoryError as e:
    say(False, 'app loads', 'OUT OF MEMORY: ' + repr(e))
except Exception as e:
    say(False, 'app loads', repr(e))
print('== done ==')
