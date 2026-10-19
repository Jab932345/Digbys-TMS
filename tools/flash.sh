#!/bin/bash
# USB-flash a unit with the v2 firmware and its own config.
#   bash tools/flash.sh /dev/tty.usbserial-XXXX tms-01
# Needs: MicroPython 1.29.0 already on the D1 Mini (see SETUP.md),
#        pip3 install mpremote "mpy-cross==1.29.0.*"
# Uses mpremote (one session, reliable); ampy proved flaky on some D1 boards.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:?usage: flash.sh <usb-port> <device-id>}"
DEV="${2:?usage: flash.sh <usb-port> <device-id>}"
CFG="firmware/configs/$DEV.py"
VER="$(tr -d '[:space:]' < firmware/VERSION)"

[ -f "$CFG" ] || { echo "No $CFG - run tools/provision.py first"; exit 1; }
command -v mpremote >/dev/null || { echo "mpremote not found: pip3 install mpremote"; exit 1; }
python3 -m py_compile "$CFG" firmware/boot.py firmware/main.py firmware/selftest.py
B="$(python3 tools/build_mpy.py)"

echo "Flashing $DEV (firmware $VER) on $PORT"
# Wipe old files first (main.py goes last, so a half-flashed board never starts the app).
mpremote connect "$PORT" exec "import os
for f in os.listdir():
    os.remove(f)" \
  + fs cp "$CFG" :config.py \
  + exec "open('fw.json','w').write('{\"v\": \"$VER\"}')" \
  + fs cp firmware/boot.py :boot.py \
  + fs cp firmware/selftest.py :selftest.py \
  + fs cp "$B/ota.mpy" :ota.mpy \
  + fs cp "$B/node.mpy" :node.mpy \
  + fs cp "$B/link.mpy" :link.mpy \
  + fs cp "$B/net.mpy" :net.mpy \
  + fs cp "$B/disp.mpy" :disp.mpy \
  + fs cp "$B/probe.mpy" :probe.mpy \
  + fs cp firmware/main.py :main.py \
  + fs ls

echo
echo "Optional check: mpremote connect $PORT exec 'import selftest'"
echo "Then press RST on the D1 Mini to start it."
