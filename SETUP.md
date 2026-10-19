# Digby's TMS v2 — setup

## How it works

```
D1 Mini units  --Wi-Fi, plain HTTP-->  Pi gateway  --HTTPS-->  Supabase  <--  digbys-tms.vercel.app
(probe, display,                    (tms-gateway,              (one shared state
 siren, button)                      port 8787)                 per unit)
```

- **One shared state per unit** lives in `temperature_live`, including the snooze end time and defrost.
  - A snooze from the website takes effect on the unit within 5 s.
  - A button press on the unit shows on the website straight away.
  - Snooze end times are set by the server clock, so the unit and the website always agree.
- **The alarm never depends on the network.** Each unit keeps its last thresholds and runs its own siren. Its button still snoozes with the Pi or Wi-Fi down, and that snooze syncs once the link is back.
- **Why the Pi gateway exists:** an ESP8266 cannot hold a modern HTTPS connection to Supabase, because its TLS library is too small. The Pi does the HTTPS side for the units.
- **Firmware updates over Wi-Fi:** you publish a version from the Mac, then press Update on the website. The unit downloads the update, checks every file's SHA-256 hash, and installs it. If the new version crashes or cannot reach the website within 6 minutes, the unit rolls back to the previous version by itself.

## Unit controls and display

| | |
|---|---|
| Short press | Snooze 30 min (1 chirp). Also shows on the website. |
| Hold 3 s | Clear snooze (2 chirps). |
| ` 4*C` | Temperature. |
| `Sn28` | Snoozed, 28 minutes left. |
| `dEF ` | Defrost on (set from the website; turns itself off after 2 h). |
| `nEt ` | No contact with the website for 30 s. The alarm still works. |
| `AUth` | This unit's key was rejected. Re-provision and reflash it. |
| `Err ` | Probe fault. This is treated as critical. |
| `UPd ` | Installing an update. |

The siren sounds once a unit has been critical for the alarm delay (default 3 minutes, set per unit on the website). It stops when the temperature recovers, or on a snooze or defrost.

---

## 1. Supabase (once)

1. Open **SQL Editor** and paste in all of `supabase/001_sync_ota.sql`. Run it.
   - It is all or nothing: if anything fails, nothing changes and the error says why.
2. Go to **Authentication → Users → Add user** and create your login, plus Wilf's.
3. Go to **Authentication → Sign In / Providers** and turn **off** "Allow new users to sign up". If you skip this, anyone could create an account and silence your alarms.

## 2. Pi gateway (once)

1. In the Flint router (192.168.8.1), go to **Clients**. Reserve a fixed IP for the Pi, for example `192.168.8.20`.
2. Copy this repo to the Pi, then run:
   ```
   sudo bash gateway/install.sh
   ```
   It finishes with `{"ok": true, ...}` and prints the Pi's IP address.
3. Check it from the Mac:
   ```
   curl http://192.168.8.20:8787/health
   ```
   After a unit has synced, `supabase_ok_s_ago` will show a small number.

## 3. Mac tools (once)

```
pip3 install adafruit-ampy esptool "mpy-cross==1.29.0.*"
```

Download `ESP8266_GENERIC-20260824-v1.29.0.bin` from https://micropython.org/download/ESP8266_GENERIC/

## 4. Each unit (×6)

The USB port shows as `/dev/tty.usbserial-XXXX`. Find it with `ls /dev/tty.usb*`.

1. **Flash MicroPython 1.29.0.** This erases the board.
   ```
   esptool.py --port /dev/tty.usbserial-XXXX erase_flash
   esptool.py --port /dev/tty.usbserial-XXXX --baud 460800 write_flash --flash_size=detect 0 ESP8266_GENERIC-20260824-v1.29.0.bin
   ```
2. **Give the unit its identity.** Do this on the Mac, in the repo folder:
   ```
   export SUPABASE_SERVICE_KEY='...'   # Supabase → Project Settings → API → service_role
   python3 tools/provision.py --device tms-01 --name "Main Freezer" --wifi "WIFI-NAME" "wifi-password" --gateway http://192.168.8.20:8787
   ```
   - For the 3 existing units, use their names exactly as they appear on the website. Their thresholds and history are kept.
   - New units need thresholds as well, for example `--warn 5 --crit 8`.
   - If the wiring differs from the table below, add `--pin PIN_RELAY 4` (or whichever pin) per change.
3. **Copy the firmware to the board:**
   ```
   bash tools/flash.sh /dev/tty.usbserial-XXXX tms-01
   ```
4. **Run the self-test.** Watch the display and listen for the siren:
   ```
   ampy --port /dev/tty.usbserial-XXXX run firmware/selftest.py
   ```
   Every line must say PASS. Do not install the unit until it does.
5. **Start it.** Press RST on the D1 Mini. Within 10 s the unit appears on the website with firmware 2.0.0.
6. **Test both directions before fitting the unit:**
   - Tap "+30m snooze" on the website. The display shows `Sn30` within 5 s.
   - Press the unit's button. The website shows the snooze within 5 s.

### Wiring (D1 Mini)

| Part | D1 Mini pin | GPIO | Note |
|---|---|---|---|
| DS18B20 data | D2 | 4 | 4.7 kΩ pull-up to VBUS. Probe powered from **VBUS**, not 3V3. |
| TM1637 CLK | D5 | 14 | |
| TM1637 DIO | D6 | 12 | Must be 12 (Pin 15's boot pull-down stops the display). |
| Relay IN (siren) | D1 | 5 | Active-low. Common ground with the 12 V supply. JD-VCC jumper off. Use channel K2. |
| Button | D7 | 13 | Momentary button to GND. No resistor needed. |

## 5. Switch the website over

Merge the pull request and Vercel deploys it. Do this on the same day as step 4: the new website only talks to v2 units.

## 6. Lock it down

Once all 6 units are on v2 and you can sign in on the website, run `supabase/002_lockdown.sql` in the SQL Editor. This removes write access for the public website key, so only signed-in users and the units' own keys can change anything.

---

## Updating firmware later

```
python3 tools/publish.py 2.0.1 --notes "what changed"
```

1. On the website, open one unit and go to **Unit firmware**. Choose 2.0.1 and press **Update this unit**.
2. Within seconds the unit shows `UPd `, restarts, and reports 2.0.1. Running then reads 2.0.1 and Last event reads "installed 2.0.1".
3. When that unit is happy, press **Update all units**.

**Protections built in:**
- A unit never updates while it is alarming. It waits until the alarm clears.
- A broken version crashes or goes silent, and the unit puts the old version back after 3 restarts. Last event then shows "rolled back 2.0.1", and that version is never retried.
- A failed download changes nothing. The unit retries every 10 minutes, up to 3 times.
- `boot.py`, `main.py`, `ota` and `config.py` are only ever changed by USB. A bad update therefore cannot stop a unit from rolling back.

## Tests

These all run on a Linux machine, not on the units.

| Command | What it proves |
|---|---|
| `python3 tests/test_sql.py` | Database rules: keys, snooze and defrost both ways, expiry, thresholds, permissions |
| `bash tests/test_migration.sh` | The migration is all or nothing, clears duplicates, and the lockdown works |
| `python3 tests/test_e2e.py` | Real firmware, real gateway and real database, end to end, including clock wrap-around |
| `MICROPYTHON=<mp32> python3 tests/test_e2e.py` | The same, on 32-bit MicroPython 1.29.0 with the ESP8266's language limits and a 38 KB heap, using the exact `.mpy` files a unit gets, including every update and rollback case |
| `python3 tests/test_dashboard.py` | The website in a headless browser: sign-in, 6 units, every button sends the right command, phone layout |
