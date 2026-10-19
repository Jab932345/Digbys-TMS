#!/bin/bash
# Installs or updates the TMS gateway on the Raspberry Pi.
# Run from this repo folder on the Pi:  sudo bash gateway/install.sh
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 7)' || { echo "Python 3.7+ needed"; exit 1; }

install -d /opt/tms-gateway
install -m 0644 tms_gateway.py /opt/tms-gateway/tms_gateway.py
[ -f /etc/tms-gateway.env ] || install -m 0644 tms-gateway.env /etc/tms-gateway.env
install -m 0644 tms-gateway.service /etc/systemd/system/tms-gateway.service

systemctl daemon-reload
systemctl enable tms-gateway >/dev/null
systemctl restart tms-gateway
sleep 2
if curl -fsS http://127.0.0.1:8787/health; then
  echo; echo "Gateway running. Pi addresses: $(hostname -I)"
else
  echo "Gateway did not start. Check: journalctl -u tms-gateway -n 50"; exit 1
fi
