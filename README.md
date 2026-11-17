# yf-scanner

Read a **2009–2015 Honda Pilot (YF3/YF4)** over OBD2 from a laptop or an Android phone, and let an AI agent do the reading with you.

An off-the-shelf ELM327 adapter (the repo assumes an ELM327 v1.5 BLE mini, a thumb-sized Bluetooth dongle) plugs into the OBD port under the dash. A small Python scanner with its own ELM327 driver reads both the engine and transmission computers. No wiring, no tools. Runs on Linux, WSL2, Windows and macOS, on a Raspberry Pi that logs every drive by itself, and on an Android phone, where an app runs the same Python.

Goals: find out what's behind noises and rough running, and catch slow drift (fuel trims, misfires, warm-up, charging, transmission temperature) before it gets expensive.

## The easy way

1. Clone this repo and open it in Claude Code, Codex or another coding agent.
2. Say: **"Help me hook up my ELM adapter to my Pilot."**

The agent reads [AGENTS.md](AGENTS.md) and walks you through it one step at a time.

**Status:** runs on a real 2011 Pilot 4WD (2026-11-16) through the reference adapter over BLE, from WSL2. Windows-native is set up but not yet run with this version. What the car answered is in [docs/honda-enhanced.md](docs/honda-enhanced.md). The Android app builds, runs the simulated Pilot and finds the adapter. A bug that stopped it connecting was fixed on 2026-11-17, but it hasn't connected to the car yet.

## What you need

- An **ELM327 v1.5 BLE mini**: the generic thumb-sized "Bluetooth 5.1, iPhone and Android" adapter. This is the type the repo is built around; ours is a ViaBecs OBD-HT. What it is, its quirks, and how to check yours: [docs/adapters.md](docs/adapters.md). Other ELM327s (USB, Bluetooth Classic, Wi-Fi, STN-based) work through the same code.
- A laptop with Bluetooth: Linux, Windows, WSL2 or macOS. It needs Python 3.10 or newer, which setup installs through [uv](https://docs.astral.sh/uv/) if it's missing. A Raspberry Pi can ride in the car and log every drive. Setup for each: [docs/platforms.md](docs/platforms.md).
- Or an Android phone (Android 8 or newer, with Bluetooth LE) running the app in [android/](android/README.md).
- The OBD port is under the dash, driver's side. No tools, no wiring.

## Use

Everything below in one terminal screen, with tabs for connecting, the health check, live values and logging with markers, Mode 06, info, a read-only console and log summaries:

```sh
.venv/bin/python tui.py --port DEMO --connect  # simulated Pilot
.venv/bin/python tui.py --connect              # the car, once an adapter is saved (below); else --port auto
```

Keys `1`-`7` switch tabs, `r` refreshes the current one, `m` drops a noise marker while recording, `q` quits. Clearing codes is on the Health tab and asks you to type `clear` first. The command line does the same things:

```sh
setup/setup.sh                                 # Linux / WSL / Pi: uv, .venv (uv sync), Bluetooth
.venv/bin/python scan.py --port DEMO --report  # simulated Pilot: prove the laptop side first
.venv/bin/python scan.py --adapters            # what's plugged in / in Bluetooth range
.venv/bin/python scan.py --adapter-info        # connect, save the adapter; its chip and link (key off is fine)

# key ON (engine off is fine); no --port needed once the adapter is saved
.venv/bin/python scan.py --report              # health check: everything below in one go
.venv/bin/python scan.py --codes               # light, stored / pending / permanent codes, readiness, both modules
.venv/bin/python scan.py --misfires            # Mode 06 misfire counters per cylinder
.venv/bin/python scan.py --monitors            # every Mode 06 test result with its limits
.venv/bin/python scan.py --supported           # which PIDs each module answers
.venv/bin/python scan.py --info                # VIN, module names, calibration IDs
.venv/bin/python scan.py --honda-probe         # Honda reads (ATF temp, VCM), raw bytes

# engine running
.venv/bin/python scan.py --tag baseline        # log a drive; Enter drops a noise marker
.venv/bin/python scan.py --set noise --honda   # noise hunting: faster rows, plus ATF and VCM
.venv/bin/python summarize.py logs/yf_XXXX.csv
```

**On a phone instead of a laptop:** the Android app in [android/](android/README.md) has the same reads, drive logging with a big MARK button, and log summaries. Its logs use the same CSV columns, so `summarize.py` on the laptop reads them too. `setup/android-sdk.sh --build` builds `android/app/build/outputs/apk/debug/app-debug.apk`: copy it to the phone and open it. With a phone on USB (USB debugging on), `--install` builds and installs it in one step.

Windows without WSL: the same commands through `setup\windows.ps1`, e.g. `powershell -ExecutionPolicy Bypass -File setup\windows.ps1 --report`.

Every command takes `--port`: `auto` (default), `ble` or `ble:<address>`, `/dev/ttyUSB0`, `COM3`, `/dev/rfcomm0`, `socket://<ip>:<port>`, or `DEMO`.

**The saved adapter.** The first time a real adapter answers, its port goes in `.adapter` at the repo root (git-ignored, so the address never lands in the repo). After that `auto` goes straight to it: no `--port`, no scan. If it doesn't answer, `auto` looks for another (serial ports, then a BLE scan) and saves whichever answers. Only when several BLE adapters are in range do you need `--port ble:<address>`, once. `--forget-adapter` deletes the file. The TUI starts on the saved adapter too (DEMO if there is none).

Each run prints the adapter's `type:`; anything other than `ELM327 v1.5 BLE mini` means it isn't the type these docs assume.

Logs go to `logs/` (git-ignored), one CSV per run, flushed every row. The live line and the CSV use dash units: mph, °F, kPa, %.

**Noise markers:** while logging, press Enter when you hear something, or type a word first (`whine`, `drone`, `clunk`). The summary lines each marker up with rpm, speed, load and the rpm/mph ratio (a gear proxy): a noise that follows rpm is engine side, one that follows road speed is wheels or driveline, one that only shows up at high load points at mounts or exhaust, and one that only shows up with VCM on three cylinders points at VCM.

**Speed:** over BLE each request takes about 0.15 s, and the scanner asks for six PIDs per request. The `drive` set (18 PIDs) is three requests, about 2 rows/s; `noise` (6 PIDs) is one, about 5 rows/s. `--honda` adds two requests per row.

## What this car answers (2011 Pilot, measured)

- **29-bit CAN, 500 kbps only.** 11-bit requests get `NO DATA`. Auto-detect finds it; `--protocol` isn't needed.
- **Two modules answer OBD requests:** `11` ECM-EngineControl (42 Mode 01 PIDs, Mode 06, VIN) and `1D` TCM-TransmisCtrl (18 PIDs, its own codes and readiness). The scanner reads both.
- **Honda reads:** ATF temperature (`22 2201`) answers at the TCM; VCM active cylinders (`22 2615`) at the ECM. Decodes are owner reports for other Hondas, marked "(candidate)" until cross-checked.
- Details, raw replies and what's still unknown: [docs/honda-enhanced.md](docs/honda-enhanced.md).

## Connecting

| Symptom | Try |
|---|---|
| Adapter not seen | Adapter lit? Phone app (YMOBD) closed? It takes one connection at a time. Bluetooth on (Linux: `sudo systemctl start bluetooth`; WSL: radio attached, see [platforms](docs/platforms.md)). Then `--adapters` |
| `BREDR ProfileUnavailable` / "No more profiles to connect to" | BlueZ is trying classic Bluetooth on an LE-only adapter. `setup/setup.sh --le-only` |
| Seen, won't connect | `--port ble:<address>` from `--adapters` (saved once it works). Unplug it from the car for 10 s and retry |
| Adapter answers, car doesn't | Key ON, adapter pushed fully in. Don't force `--protocol` (the Pilot only answers 29-bit) |
| Slow or dropped replies | Raise `--timeout` (default 1.0 s). Move the laptop closer. `--raw` shows every line |
| BLE keeps failing, adapter is dual-mode | Use its Classic side: pair (PIN 1234), `sudo rfcomm bind 0 <address>`, then `--port /dev/rfcomm0` (Windows: its COM port) |
| USB adapter | `--port /dev/ttyUSB0` (or `COM3`). Linux: you need the `dialout` (or `uucp`) group; `setup.sh` adds it |

The log summary line reports link quality: how many requests were retried because the BLE link dropped a frame, and how many timed out.

## Safety

- Everything except `--clear-codes` only reads. `--query` refuses any service that isn't a read (01, 02, 03, 06, 07, 09, 0A, 22).
- `--clear-codes` asks first. It resets every readiness monitor and the learned fuel trims; the car fails an emissions test until the monitors complete again. Permanent codes stay until the car itself confirms the fix.
- Don't fiddle with the laptop while driving. Have a passenger press the marker key, or say the noise out loud and mark it at the next stop.
- No actuator tests. Honda's are not public; see [docs/honda-enhanced.md](docs/honda-enhanced.md) for how they'd be captured, and the rules for it.

## Files

| Path | What |
|---|---|
| `scan.py` | The command line: report, codes, misfires, monitors, supported, info, Honda probe, read-only query, logging |
| `android/` | The Android app: the same reads, logging with a MARK button, and summaries on a phone. Build: [android/README.md](android/README.md) |
| `tui.py` | The terminal UI (Textual) over the same layers: connect, health, live + CSV log, Mode 06, info, console, log summaries |
| `summarize.py` | A drive log's timeline, warm-up, charging, trims by condition (coasting apart), bank difference, long-term trim changes, VCM, markers |
| `scanner/` | The library the three commands and the Android app use |
| `scanner/car.py` | The car: finds the modules, reads each one by its own address, decodes |
| `scanner/report.py` | Each read as titled sections of lines: `scan.py` prints them, `tui.py` styles them, the app shows them as cards |
| `scanner/phone.py` | The Android app's entry point into the scanner (Chaquopy): connect, reads, drive log, summary, as JSON |
| `scanner/honda.py` | The Pilot's module addresses, Honda Mode 22 candidates and which module answers each |
| `scanner/logger.py` | Drive logs: CSV, markers (keyboard or Pi button), reconnect after a dropped link, unattended mode |
| `scanner/elm/` | The adapter: `session.py` (the ELM327 dialogue, per-module requests, ISO-TP), `adapter.py` (ports, identifying the chip), `ble.py` (BLE transport) |
| `scanner/obd2/` | SAE J1979 decoding: Mode 01 PIDs, readiness, trouble codes, Mode 06 |
| `scanner/sim/` | Simulated reference adapter + 2011 Pilot, modeled on the real one (`--port DEMO`, tests) |
| `tests/` | Parser tests on captured replies; car, CLI, TUI, logger, transport and phone-app bridge tests against the simulator |
| `setup/` | `setup.sh` (Linux/WSL/Pi), `windows.ps1`, `wsl-attach-bluetooth.ps1`, `pi-logger.sh`, `android-sdk.sh` (builds the app) |
| `docs/` | [adapters](docs/adapters.md), [platforms](docs/platforms.md), [honda-enhanced](docs/honda-enhanced.md) |
| `logs/` | Drive logs (git-ignored) |

```sh
.venv/bin/python -m unittest discover -s tests  # no hardware needed
```

## To do

- Confirm the Honda decodes: ATF against a thermocouple on the cooler line; VCM against the ECO light on a light steady cruise (expect 3 cylinders).
- A first real drive log (`--tag baseline --honda`): trims at cruise, VCM behavior, ATF under load.
- Connect the Android app to the car through the reference adapter, and log a drive with it.
- Capture-and-decode tooling for Honda traffic (CANable / SocketCAN), for anything the dealer tool reads that Mode 22 candidates don't cover.

## Credits and license

Forked from [MZULALI/xj-scanner](https://github.com/MZULALI/xj-scanner), a scanner for 1991–95 Jeeps; the approach (a small Python tool, plain CSV logs, an agent that reads them with you) comes from there. GPLv3.

This is a hobby project. It talks to your car's computers; you use it at your own risk.
