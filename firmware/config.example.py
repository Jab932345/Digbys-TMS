# Per-unit settings. tools/provision.py writes a filled-in copy for each unit;
# it is uploaded to the D1 Mini as config.py and is never updated over the air.

DEVICE_ID = 'tms-01'
DEVICE_KEY = 'replace-me'

# Wi-Fi networks to try, in order.
WIFI = [
    ('SSID', 'password'),
]

# The TMS gateway on the Raspberry Pi (fixed IP set in the router).
GATEWAY = 'http://192.168.8.20:8787'

# Pins are GPIO numbers. D1 Mini labels: D1=5 D2=4 D5=14 D6=12 D7=13.
PIN_PROBE = 4       # D2  DS18B20 data (4.7k pull-up to VBUS)
PIN_TM_CLK = 14     # D5  TM1637 CLK
PIN_TM_DIO = 12     # D6  TM1637 DIO (must be 12)
PIN_RELAY = 5       # D1  relay IN for the siren
PIN_BUTTON = 13     # D7  push button to GND

RELAY_ACTIVE_LOW = True
DISPLAY_HAS_DP = False   # True if your display has decimal points (selftest shows 8.8.8.8)
BRIGHTNESS = 4           # 0-7

# Used only until the first contact with the gateway; after that the unit
# follows the thresholds set on the website (cached across restarts).
FALLBACK_WARN = 8.0
FALLBACK_CRIT = 10.0
FALLBACK_DELAY_S = 180

SNOOZE_MIN = 30          # short press on the button
