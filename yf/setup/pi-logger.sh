#!/usr/bin/env bash
# Install the unattended drive logger as a systemd service on a Raspberry Pi (or any Linux box
# that rides in the car). Run yf/setup/setup.sh first.
#
#   yf/setup/pi-logger.sh --port ble:AA:BB:CC:DD:EE:FF [--gpio-button 17] [--honda] [--set noise]
#   yf/setup/pi-logger.sh --remove
#
# The service waits for the adapter and a running engine, logs each drive to yf/logs/ as its own
# CSV, and closes the file 20 s after the engine stops. journalctl -u yf-logger -f shows what it's doing.
set -euo pipefail

repo="$(cd "$(dirname "$0")/../.." && pwd)"
unit=/etc/systemd/system/yf-logger.service

if [ "${1:-}" = "--remove" ]; then
  sudo systemctl disable --now yf-logger 2>/dev/null || true
  sudo rm -f "$unit"
  sudo systemctl daemon-reload
  echo "removed yf-logger"
  exit 0
fi
if [ $# -eq 0 ] || ! printf '%s\n' "$@" | grep -q -- '--port'; then
  sed -n 2,10p "$0"
  echo
  echo "Give --port (find it with: $repo/.venv/bin/python $repo/yf/yf_scan.py --adapters)"
  exit 2
fi
[ -x "$repo/.venv/bin/python" ] || { echo "no venv; run yf/setup/setup.sh first"; exit 1; }

groups_line=""
getent group gpio >/dev/null && groups_line="SupplementaryGroups=gpio"

sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=yf-scanner unattended drive logger
After=bluetooth.target
Wants=bluetooth.target

[Service]
User=$USER
WorkingDirectory=$repo
ExecStart=$repo/.venv/bin/python -u $repo/yf/yf_scan.py --unattended $*
Restart=always
RestartSec=10
$groups_line

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now yf-logger
sleep 2
systemctl --no-pager status yf-logger | head -8
echo
echo "Logs: $repo/yf/logs/   Service output: journalctl -u yf-logger -f"
