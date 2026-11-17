---
name: yf-scan
description: Connect an ELM327 adapter (the repo assumes an ELM327 v1.5 BLE mini) to a 2009-2015 Honda Pilot, read codes, readiness, misfire counters and Mode 06 results from the engine and transmission modules, log drives with noise markers, and summarize what the logs show. Use whenever the person wants to set up, scan or log their Pilot.
---

# Read the Pilot

The scanner is `yf/yf_scan.py`, run from the repo root with the venv's Python. Read [yf/README.md](../../../yf/README.md) first. The person may be new to this: say what a thing is the first time you name it, and wait for them to report what they see. For multi-step setup, hand them a script from `yf/setup/` (or write one there) rather than a list of commands.

## First time

1. Set up the computer, per [yf/docs/platforms.md](../../../yf/docs/platforms.md):
   - Linux or Raspberry Pi: `yf/setup/setup.sh`.
   - WSL2: in Windows, `yf\setup\wsl-attach-bluetooth.ps1` (it takes Bluetooth away from Windows while attached). Then `yf/setup/setup.sh` in WSL.
   - Windows native: `yf\setup\windows.ps1 <args>` does setup and runs the scanner.
2. `.venv/bin/python yf/yf_scan.py --port DEMO --report`. This must work before the car is involved.
3. Adapter into the OBD port under the dash. Close any phone app connected to it first; it takes one connection at a time.
4. `--adapters`, then `--port ble:<address> --adapter-info` (key off is fine). Check the `type:` it prints. If it isn't `ELM327 v1.5 BLE mini`, tell them, and read the `note:` lines for what that changes.
5. Key ON (engine off is fine): `--port <port> --report`. If it won't connect, use the "Connecting" table in the README, one change at a time. Pass the same `--port` to every command after this. Don't force `--protocol`: the Pilot only answers 29-bit CAN, and auto-detect finds it.
6. If this car isn't the 2011 already recorded, save `--report`, `--supported`, `--info` (leave the VIN out of the repo) and `--honda-probe`. Record what differs in [yf/docs/honda-enhanced.md](../../../yf/docs/honda-enhanced.md).

## Common asks

| They ask | Run | Then |
|---|---|---|
| "Is it OK?" / "Check it over" | `--report` | Go through each section: codes per module, incomplete monitors, uneven misfire counts, Mode 06 failures, trims, charging voltage, ATF. Say what each can and can't tell |
| "Any codes?" | `--codes` | Give each code, which module, whether it's stored, pending or permanent, and which readiness monitors are incomplete |
| "Log my drive" | `--tag <word> --honda`; Ctrl+C at the end | Remind them: Enter = marker, a word first labels it. Passenger works the laptop |
| "Chasing a noise" | `--set noise --honda --tag noise` | Six PIDs in one request, so rows come fast. Ask them to mark every time they hear it, and to try to reproduce it at different gears, speeds and loads, and with VCM on or off (light steady cruise vs. accelerating) |
| "Summarize that log" | `.venv/bin/python yf/tools/summarize.py yf/logs/<file>.csv` | Then read the CSV yourself for anything the summary doesn't cover |
| "Misfires?" | `--misfires` | Per-cylinder counts. Uneven counts on one cylinder are worth following up (plug, coil, injector) |
| "Transmission temp?" | `--honda-probe`, or log with `--honda` | ATF is a candidate decode: plausible on the 2011, not cross-checked |
| "Log every drive" (Pi) | `yf/setup/pi-logger.sh --port ble:<address> --honda` | See the Pi section of platforms.md: power, clock, the marker button |
| Something odd on the link | add `--raw` | Every line to and from the adapter. The log's last line counts retries and timeouts |

## Reading the numbers honestly

- Report what the data shows and what it can't. OBD sees the engine and transmission computers, not suspension. A clunk over bumps won't appear in any column.
- Fuel trims: compare banks and conditions (idle vs. cruise) rather than single values. Lean only at idle often means a vacuum leak; lean everywhere often means fuel delivery or the airflow reading. These are rules of thumb, not Honda specs.
- Mode 06: a result inside its limits passes; the Honda test IDs (80 and up) aren't published, so name them by number, not by guess.
- Anything marked "(candidate)" is an unconfirmed Honda decode. Don't draw conclusions from it until it's been cross-checked.
- Don't guess at a diagnosis. Say what to check next and why.

## Things that change the car: ask first

- `--clear-codes` resets readiness monitors and fuel trims. Ask, and remind them about emissions testing, before running it.
- Nothing in this repo runs actuator tests or writes to a module. `--query` refuses non-read services. Don't improvise Honda commands. Follow the sniff-and-replay rules in [yf/docs/honda-enhanced.md](../../../yf/docs/honda-enhanced.md).
