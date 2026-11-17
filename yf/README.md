# yf: 2009–2015 Honda Pilot (YF3/YF4)

The 2nd-gen Pilot is full OBD2, so an off-the-shelf **ELM327 adapter** and [python-OBD](https://github.com/brendan-w/python-OBD) are all it takes: a small Python tool, plain CSV logs, and an agent that reads them with you.

Goals: find out what's behind noises and rough running, and catch slow drift (fuel trims, misfires, warm-up, charging) before it gets expensive.

**Status: groundwork, not yet run on a real Pilot.** Everything below works against the simulated car (`--port DEMO`).

## What you need

- An **ELM327 v1.5 BLE mini**: the generic thumb-sized "Bluetooth 5.1, iPhone and Android" adapter, a clone on a PIC18F25K80 chip. This is the type the repo is built around; ours is a ViaBecs OBD-HT (type inferred from listings, `(unverified)`). What it is and how to confirm yours: [docs/adapters.md](docs/adapters.md). Other ELM327s (USB, Bluetooth Classic, Wi-Fi, STN-based) work through the same code; cut-down v2.1 clones are the ones likely to fail Honda-only reads.
- Bluetooth on the laptop (Linux: BlueZ, `systemctl start bluetooth`).
- A laptop with Python 3.
- The OBD port is under the dash, driver's side. No tools, no wiring.

## Use

```sh
python3 -m venv .venv && .venv/bin/pip install -r yf/requirements.txt
.venv/bin/python yf/yf_scan.py --port DEMO --codes     # simulated Pilot: prove the laptop side first
.venv/bin/python yf/yf_scan.py --adapters              # what's plugged in / in Bluetooth range
.venv/bin/python yf/yf_scan.py --port ble --adapter-info    # the adapter's chip and link (key off is fine)

# key ON (engine off is fine for these)
.venv/bin/python yf/yf_scan.py --codes                 # check engine light, stored + pending codes, readiness
.venv/bin/python yf/yf_scan.py --supported             # which standard PIDs this car answers
.venv/bin/python yf/yf_scan.py --misfires              # Mode 06 misfire counters per cylinder
.venv/bin/python yf/yf_scan.py --protocol 7 --honda-probe   # Honda reads (ATF temp, VCM) on 29-bit, raw bytes

# engine running
.venv/bin/python yf/yf_scan.py --tag baseline          # log a drive; Enter drops a noise marker
.venv/bin/python yf/yf_scan.py --protocol 7 --set noise --honda   # noise hunting, plus Honda candidates
                                                       # (add --honda only once --honda-probe gets answers; silent reads slow every row)
.venv/bin/python yf/tools/summarize.py yf/logs/yf_XXXX.csv
```

Every command takes `--port`: `auto` (default: serial ports, then a BLE scan), `ble` or `ble:<address>`, `/dev/ttyUSB0`, `/dev/rfcomm0`, or `socket://<ip>:<port>`. Once the adapter's address is known, pass `--port ble:<address>` to skip the scan. Each run prints the adapter's `type:`; anything other than `ELM327 v1.5 BLE mini` means it isn't the type these docs assume.

Logs go to `yf/logs/` (git-ignored), one CSV per run, flushed every row.

**Noise markers:** while logging, press Enter when you hear something, or type a word first (`whine`, `drone`, `clunk`). The summary lines each marker up with rpm, speed, load and the rpm/mph ratio (a gear proxy): a noise that follows rpm is engine side, one that follows road speed is wheels or driveline, and one that only shows up at high load points at mounts or exhaust.

## Connecting

| Symptom | Try |
|---|---|
| Adapter not seen | Adapter lit? Phone app (YMOBD) closed? It takes one connection at a time. `systemctl start bluetooth`, then `--adapters` |
| Seen, won't connect | `--port ble:<address>` from `--adapters`. Unplug it from the car for 10 s and retry |
| Adapter found, car not | Key ON, adapter pushed fully in. Then `--protocol 6` (CAN 11-bit 500k, expected for this car `(unverified)`) |
| Refuses on battery voltage | `--no-voltage-check` (some clones misreport it) |
| Slow or dropped replies | Raise `--timeout` (default 0.5 s). Leave `--fast` off with clones |
| BLE keeps failing, adapter is dual-mode | Use its Classic side: pair (PIN 1234), `sudo rfcomm bind 0 <address>`, then `--port /dev/rfcomm0` |
| USB adapter instead | `--port /dev/ttyUSB0`. Linux: add yourself to `dialout` (or `uucp`), log out and in |

"v1.5" is clone firmware; ELM Electronics never made a v1.5. On the PIC18F25K80 minis it's reputed to be the better class of clone, but some clones reject AT commands python-OBD sends.

## Safety

- Everything except `--clear-codes` only reads. Mode 22 Honda reads are read requests too.
- `--clear-codes` asks first. It resets every readiness monitor and the learned fuel trims; the car fails an emissions test until the monitors complete again.
- Don't fiddle with the laptop while driving. Have a passenger press the marker key, or say the noise out loud and mark it at the next stop.
- No actuator tests yet. Honda's are not public; see [docs/honda-enhanced.md](docs/honda-enhanced.md) for how they'd be captured, and the rules for it.

## Files

| Path | What |
|---|---|
| `yf_scan.py` | Codes, readiness, supported PIDs, Mode 06 misfires, drive logging with markers, DEMO car |
| `elm/` | The adapter layer: port names, connect, chip identification, adapter listing; BLE transport (`ble.py`) |
| `tests/` | ELM327 simulator, and tests of `elm/` over simulated BLE and a pty |
| `docs/adapters.md` | The reference adapter type and our ViaBecs, transports, how BLE pipes are found, first-contact checklist |
| `honda.py` | Candidate Honda Mode 22 reads with raw-byte probe; 29-bit `DA1DF1` addressing under `--protocol 7` |
| `tools/summarize.py` | Warm-up, charging, trims by condition, bank difference, markers |
| `docs/honda-enhanced.md` | What's known about Honda-only data on this generation, and the sniff-and-replay plan |

## To do

- First contact with the ViaBecs: the checklist in [docs/adapters.md](docs/adapters.md). Confirm it reports the reference type; record results there.
- First real run: `--supported`, `--codes`, `--misfires`, then `--protocol 7 --honda-probe`; record the results in the docs.
- Unattended mode for a Raspberry Pi: wait for the PCM, one file per drive, GPIO button for markers. The Pilot's 12 V outlets are live only in ACC/ON (2011 owner's manual p. 190), so a car charger won't drain the battery.
- Capture-and-decode tooling for Honda traffic (CANable / SocketCAN).
