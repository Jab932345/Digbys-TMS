#!/bin/bash
# USB-flash a unit with the v2 firmware and its own config.
#   bash tools/flash.sh /dev/tty.usbserial-XXXX tms-01
# Needs: MicroPython 1.29.0 already on the D1 Mini (see SETUP.md),
#        pip3 install adafruit-ampy "mpy-cross==1.29.0.*"
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:?usage: flash.sh <usb-port> <device-id>}"
DEV="${2:?usage: flash.sh <usb-port> <device-id>}"
CFG="firmware/configs/$DEV.py"
VER="$(tr -d '[:space:]' < firmware/VERSION)"
export AMPY_DELAY="${AMPY_DELAY:-1}"     # D1 Minis need a pause to enter the raw REPL
A=(ampy --port "$PORT")

[ -f "$CFG" ] || { echo "No $CFG - run tools/provision.py first"; exit 1; }
command -v ampy >/dev/null || { echo "ampy not found: pip3 install adafruit-ampy"; exit 1; }
python3 -m py_compile "$CFG" firmware/boot.py firmware/main.py firmware/selftest.py
BUILD="$(python3 tools/build_mpy.py)"

echo "Flashing $DEV (firmware $VER) on $PORT"
# main.py is removed first and copied last, so a half-flashed board never starts the app.
"${A[@]}" rm main.py 2>/dev/null || true
for f in fw.json fw.json.tmp crash.txt cache.json; do "${A[@]}" rm "$f" 2>/dev/null || true; done
for m in node link net disp probe ota; do
  for f in "$m.py" "$m.py.bak" "$m.mpy.bak" "$m.mpy.new"; do "${A[@]}" rm "$f" 2>/dev/null || true; done
done

"${A[@]}" put "$CFG" config.py
TMP="$(mktemp)"; printf '{"v": "%s"}' "$VER" > "$TMP"
"${A[@]}" put "$TMP" fw.json; rm -f "$TMP"
for f in boot.py selftest.py; do echo "  $f"; "${A[@]}" put "firmware/$f" "$f"; done
for m in ota node link net disp probe; do echo "  $m.mpy"; "${A[@]}" put "$BUILD/$m.mpy" "$m.mpy"; done
"${A[@]}" put firmware/main.py main.py

echo "Files on the board:"; "${A[@]}" ls
echo
echo "Next: ampy --port $PORT run firmware/selftest.py"
echo "Then press RST on the D1 Mini (or unplug and replug) to start it."
