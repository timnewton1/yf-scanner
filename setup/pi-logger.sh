#!/usr/bin/env bash
# Install the unattended drive logger as a systemd service on a Raspberry Pi (or any Linux box
# that rides in the car). Run setup/setup.sh first.
#
#   setup/pi-logger.sh [--gpio-button 17] [--honda] [--quick] [--set noise] [--port ...]
#   setup/pi-logger.sh --remove
#
# The adapter: the one saved in .adapter (connect once with scan.py --adapter-info and it is), or
# --port. Without either the service scans for one on every try.
#
# The service waits for the adapter and a running engine, logs each drive to logs/ as its own
# CSV, and closes the file 20 s after the engine stops. journalctl -u yf-logger -f shows what it's doing.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
unit=/etc/systemd/system/yf-logger.service

if [ "${1:-}" = "--remove" ]; then
  sudo systemctl disable --now yf-logger 2>/dev/null || true
  sudo rm -f "$unit"
  sudo systemctl daemon-reload
  echo "removed yf-logger"
  exit 0
fi
[ -x "$repo/.venv/bin/python" ] || { echo "no venv; run setup/setup.sh first"; exit 1; }
if ! printf '%s\n' "$@" | grep -q -- '--port'; then
  if [ -s "$repo/.adapter" ]; then
    echo "adapter: $(grep -v '^#' "$repo/.adapter" | head -1) (saved in .adapter)"
  else
    echo "No saved adapter yet, so the service will scan on every try. To save one, connect once:"
    echo "  $repo/.venv/bin/python $repo/scan.py --adapter-info"
  fi
fi

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
ExecStart=$repo/.venv/bin/python -u $repo/scan.py --unattended $*
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
echo "Logs: $repo/logs/   Service output: journalctl -u yf-logger -f"
