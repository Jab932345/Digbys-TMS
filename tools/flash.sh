#!/bin/bash
# USB-flash a unit with the v2 firmware and its own config.
#   bash tools/flash.sh /dev/tty.usbserial-XXXX tms-01
# Needs: MicroPython 1.29.0 already on the D1 Mini (see SETUP.md),
#        pip3 install mpremote "mpy-cross==1.29.0.*"
#
# Each file is copied, then its SHA-256 is checked on the board. A failed or
# corrupted copy is retried (up to 6 times), so a flaky USB link still ends
# with a verified, complete install.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${1:?usage: flash.sh <usb-port> <device-id>}"
DEV="${2:?usage: flash.sh <usb-port> <device-id>}"
CFG="firmware/configs/$DEV.py"
VER="$(tr -d '[:space:]' < firmware/VERSION)"
TRIES=12

[ -f "$CFG" ] || { echo "No $CFG - run tools/provision.py first"; exit 1; }
command -v mpremote >/dev/null || { echo "mpremote not found: pip3 install mpremote"; exit 1; }
python3 -m py_compile "$CFG" firmware/boot.py firmware/main.py firmware/selftest.py || exit 1
B="$(python3 tools/build_mpy.py)" || exit 1

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '{"v": "%s"}' "$VER" > "$TMP/fw.json"

mp() { mpremote connect "$PORT" "$@" 2>&1; }

retry() {   # retry <description> <command...>
  local what="$1"; shift
  for i in $(seq 1 $TRIES); do
    if out="$("$@")"; then return 0; fi
    echo "    $what failed (try $i/$TRIES), retrying..."
    sleep 1
  done
  echo "    $what failed $TRIES times: $out"
  # Never leave a half-copied main.py: without it the board just sits at the prompt.
  for i in 1 2 3 4 5; do mp exec "import os
try:
    os.remove('main.py')
except OSError:
    pass" >/dev/null && break; done
  echo "FAILED. Unplug the board for 5 s, plug it back in and run this again."
  exit 1
}

wipe() {
  out="$(mp exec "import os
for f in os.listdir():
    os.remove(f)
print('WIPED', len(os.listdir()))")" && echo "$out" | grep -q "WIPED 0"
}

put() {   # put <local> <remote>
  local src="$1" dst="$2" want got
  want="$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$src")"
  mp fs cp "$src" ":$dst" >/dev/null || return 1
  got="$(mp exec "import hashlib,binascii
h=hashlib.sha256()
f=open('$dst','rb')
while True:
    b=f.read(256)
    if not b: break
    h.update(b)
f.close()
print('SHA', binascii.hexlify(h.digest()).decode())" | grep -o 'SHA [0-9a-f]\{64\}' | cut -c5-)"
  [ "$got" = "$want" ]
}

echo "Flashing $DEV (firmware $VER) on $PORT"
retry "wipe" wipe
echo "  board wiped"
# main.py goes last, so a half-flashed board never starts the app.
for pair in "$CFG:config.py" "$TMP/fw.json:fw.json" "firmware/boot.py:boot.py" \
            "firmware/selftest.py:selftest.py" "$B/ota.mpy:ota.mpy" "$B/node.mpy:node.mpy" \
            "$B/link.mpy:link.mpy" "$B/net.mpy:net.mpy" "$B/disp.mpy:disp.mpy" \
            "$B/probe.mpy:probe.mpy" "firmware/main.py:main.py"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  retry "$dst" put "$src" "$dst"
  echo "  $dst  ok (verified)"
done

echo
echo "All 11 files copied and verified."
echo "Press RST on the D1 Mini to start it."
