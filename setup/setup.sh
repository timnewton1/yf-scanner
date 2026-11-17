#!/usr/bin/env bash
# Set up yf-scanner on Linux, WSL2 or a Raspberry Pi: uv, the .venv (uv sync), Bluetooth.
#
#   setup/setup.sh            uv + .venv + BlueZ running
#   setup/setup.sh --le-only  also switch BlueZ to Bluetooth LE only (fixes "BREDR ProfileUnavailable"
#                             with LE-only adapters; turns off classic Bluetooth on this machine)
#
# On WSL, --le-only is the default (WSL has no other Bluetooth users), and the radio must first be
# handed to WSL from Windows: setup/wsl-attach-bluetooth.ps1.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
le_only=0
is_wsl=0
grep -qi microsoft /proc/version 2>/dev/null && is_wsl=1
for a in "$@"; do
  case "$a" in
    --le-only) le_only=1 ;;
    --no-le-only) le_only=-1 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done
[ "$is_wsl" = 1 ] && [ "$le_only" = 0 ] && le_only=1
[ "$le_only" = -1 ] && le_only=0

say() { printf '\n== %s\n' "$*"; }

say "Python (uv)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "installing uv (Python and venv manager) into ~/.local/bin"
  command -v curl >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y curl; }
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
cd "$repo"
if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
  uv sync --extra pi || { echo "(gpiozero not installed; the marker button won't work)"; uv sync; }
else
  uv sync
fi
"$repo/.venv/bin/python" "$repo/scan.py" --port DEMO --codes >/dev/null && echo "venv OK, DEMO runs"

say "Bluetooth (BlueZ)"
if ! command -v bluetoothctl >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y bluez
fi
conf=/etc/bluetooth/main.conf
if [ "$le_only" = 1 ] && [ -f "$conf" ] && ! grep -q '^ControllerMode *= *le' "$conf"; then
  echo "setting ControllerMode = le in $conf"
  sudo sed -i -E 's/^#? *ControllerMode *=.*/ControllerMode = le/' "$conf"
  grep -q '^ControllerMode' "$conf" || echo 'ControllerMode = le' | sudo tee -a "$conf" >/dev/null
fi
if [ -d /run/systemd/system ]; then
  sudo systemctl enable --now bluetooth >/dev/null 2>&1 || true
  sudo systemctl restart bluetooth
else
  sudo service bluetooth restart || sudo service bluetooth start
fi
sleep 2

say "USB adapters"
if ! id -nG | grep -qwE 'dialout|uucp'; then
  grp=$(getent group dialout >/dev/null && echo dialout || echo uucp)
  echo "adding $USER to $grp (for USB adapters; log out and back in for it to take)"
  sudo usermod -aG "$grp" "$USER" || true
fi

say "Status"
if command -v hciconfig >/dev/null && hciconfig 2>/dev/null | grep -q hci; then
  hciconfig | grep -E '^hci|UP|DOWN' | head -3
elif bluetoothctl list 2>/dev/null | grep -q Controller; then
  bluetoothctl list
else
  echo "No Bluetooth radio visible."
  if [ "$is_wsl" = 1 ]; then
    echo "WSL: run setup/wsl-attach-bluetooth.ps1 in Windows first (it hands the radio to WSL), then this again."
  fi
  exit 1
fi
echo
echo "Next: $repo/.venv/bin/python $repo/scan.py --adapters"
