# Runs first on every power-up. Flashed by USB only.
# 1. Holds the siren relay off before anything else can run.
# 2. Lets ota.py count boots of a new version and roll it back if it keeps failing.
import gc

try:
    import esp
    esp.osdebug(None)
except Exception:
    pass

try:
    import config
    from machine import Pin
    Pin(config.PIN_RELAY, Pin.OUT, value=1 if config.RELAY_ACTIVE_LOW else 0)
except Exception:
    pass

try:
    import ota
    BOOT_STATUS = ota.boot_check()
except Exception as e:
    BOOT_STATUS = 'boot_check error: %s' % e

# A firmware update asked for by the website is installed here, before the app
# is loaded, so it has the memory to itself. Success restarts the board.
try:
    if ota.in_request():
        import net
        gc.collect()
        ota.run_request(net)
except Exception:
    pass

gc.collect()
