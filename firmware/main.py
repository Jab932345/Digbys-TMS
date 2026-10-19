# Starts the unit. Flashed by USB only.
# If the app crashes, the board restarts after 5 s. A new version that crashes
# or never reaches the website is rolled back automatically (see ota.py).
import gc
import time
import machine
import ota

gc.collect()

if ota.in_trial():
    try:
        _t = machine.Timer(-1)
        _t.init(period=360000, mode=machine.Timer.ONE_SHOT, callback=ota.trial_timeout)
    except Exception:
        pass

try:
    import node
    gc.collect()
    node.run()
except KeyboardInterrupt:
    # Ctrl-C from the USB connection (ampy) stops the app. Siren off first.
    try:
        import config
        machine.Pin(config.PIN_RELAY, machine.Pin.OUT, value=1 if config.RELAY_ACTIVE_LOW else 0)
    except Exception:
        pass
    raise
except Exception as e:
    try:
        import config
        machine.Pin(config.PIN_RELAY, machine.Pin.OUT, value=1 if config.RELAY_ACTIVE_LOW else 0)
    except Exception:
        pass
    try:
        with open('crash.txt', 'w') as f:
            f.write('%s: %s\n' % (type(e).__name__, e))
    except Exception:
        pass
    time.sleep(5)
    machine.reset()
