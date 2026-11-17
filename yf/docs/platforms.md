# Platforms: where the scanner runs

The scanner is plain Python plus pyserial, and bleak for BLE. It runs anywhere those do. What differs is how the computer reaches a BLE adapter.

| Platform | BLE through | Setup | Status |
|---|---|---|---|
| Linux (desktop) | BlueZ | `yf/setup/setup.sh` | Expected to work like WSL below; not yet run on a desktop |
| WSL2 on Windows | BlueZ in WSL, radio passed in by usbipd | `yf\setup\wsl-attach-bluetooth.ps1` in Windows, then `yf/setup/setup.sh` in WSL | **Works** (2011 Pilot, Intel AX radio, Ubuntu 26.04, 2026-11-16) |
| Windows | Windows Bluetooth (WinRT) | `yf\setup\windows.ps1 <args>` | First contact worked here (same day, same adapter) with the earlier python-OBD scanner; the current driver and `windows.ps1` not yet run on Windows |
| macOS | CoreBluetooth | venv + `pip install -r yf/requirements.txt` | Untested. BLE addresses on macOS are per-machine UUIDs: a `.adapter` saved on another OS won't match, and `auto` falls back to a scan and saves the new one |
| Raspberry Pi | BlueZ | `yf/setup/setup.sh`, then `yf/setup/pi-logger.sh` | Untested on a Pi; the unattended mode is tested against the simulator |

## WSL2

WSL has no Bluetooth of its own. `usbipd-win` lends it the laptop's USB Bluetooth radio, and **Windows loses Bluetooth while it's lent**.

1. Windows: `powershell -ExecutionPolicy Bypass -File yf\setup\wsl-attach-bluetooth.ps1`. It elevates itself, installs usbipd-win if needed, finds the Bluetooth radio, and attaches it to WSL. To give it back: run it again with `-Detach`.
2. WSL: `yf/setup/setup.sh`. It installs `python3-venv` and `bluez` if they're missing, builds `.venv`, sets BlueZ to **LE only**, and restarts it.
3. `.venv/bin/python yf/yf_scan.py --adapters`.

Why LE only: BlueZ in dual mode tries to connect the adapter over classic Bluetooth first, and bleak fails with `org.bluez.Error.BREDR.ProfileUnavailable: No more profiles to connect to`. `ControllerMode = le` in `/etc/bluetooth/main.conf` fixes it. On WSL nothing else uses Bluetooth. On a desktop it turns off classic Bluetooth (headphones), so `setup.sh` only does it there with `--le-only`.

The stock WSL2 kernel (6.18) has the needed modules: `CONFIG_BT=m`, `CONFIG_BT_HCIBTUSB=m`, and Intel, Broadcom and Realtek support.

If you'd rather keep Windows Bluetooth, run the scanner on Windows instead (next section). Both can use the same checkout.

## Windows

`yf\setup\windows.ps1` installs Python with winget if there is none, makes a venv in `%LOCALAPPDATA%\yf-scanner\venv`, installs the requirements, and passes its arguments to `yf_scan.py`:

```powershell
powershell -ExecutionPolicy Bypass -File yf\setup\windows.ps1 --adapters
powershell -ExecutionPolicy Bypass -File yf\setup\windows.ps1 --report
```

It works from a WSL checkout too (`\\wsl.localhost\Ubuntu\home\<you>\repos\yf-scanner\yf\setup\windows.ps1`): the venv stays on the Windows side, and the logs land in the same `yf/logs/`, and the saved `.adapter` is shared (WSL and Windows both name a BLE adapter by its address). The radio must not be attached to WSL at the time.

USB and Bluetooth Classic adapters show up as COM ports: `--port COM3`. `--adapters` lists them.

## Raspberry Pi (in the car)

The idea: a Pi powered from a 12 V outlet, which is live only in ACC/ON (2011 owner's manual p. 190), so it can't drain the battery. It logs every drive by itself.

```sh
yf/setup/setup.sh                                     # venv, bluez, gpiozero
.venv/bin/python yf/yf_scan.py --adapter-info         # finds the adapter and saves it in .adapter
yf/setup/pi-logger.sh --honda --gpio-button 17        # the service uses the saved adapter
journalctl -u yf-logger -f                            # watch it
```

`--unattended` waits for the adapter and a running engine (rpm above 300). It logs to its own `yf_<date>_<time>_auto.csv` and closes the file 20 s after the engine stops, then waits for the next drive. A dropped BLE link is retried for 60 s inside the same file before it gives up on that drive.

**Marker button:** a push button between the GPIO pin (BCM numbering, e.g. 17) and ground. Each press is a `button` marker in the log.

Things to know:
- **Power cut at key off.** The outlet dies with the key, so the Pi loses power mid-file. Rows are flushed every row and synced to the card every 10 rows, so at most the last few seconds are lost. An SD card can still be corrupted by hard power-offs; a small UPS HAT, or a read-only root with logs on a separate partition, avoids that.
- **Clock.** A Pi has no real-time clock. Until it gets network time, file names and `time` columns may be wrong. `elapsed_s` is always right.
- **Range.** The adapter is under the dash; a Pi in the console is well within BLE range.
