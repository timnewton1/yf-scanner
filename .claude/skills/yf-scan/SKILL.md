---
name: yf-scan
description: Connect an ELM327 adapter to a 2009-2015 Honda Pilot, read codes, readiness and misfire counters, log drives with noise markers, and summarize what the logs show. Use for the Honda Pilot side of this repo (yf/), not the Jeep.
---

# Read the Pilot

The scanner is `yf/yf_scan.py`, run from the repo root with the venv's Python. Read [yf/README.md](../../../yf/README.md) first. The person may be new to this: say what a thing is the first time you name it, and wait for them to report what they see.

## First time

1. `python3 -m venv .venv && .venv/bin/pip install -r yf/requirements.txt`
2. `.venv/bin/python yf/yf_scan.py --port DEMO --codes`. This must work before the car is involved.
3. Adapter into the laptop, then into the OBD port under the dash, key ON.
4. `.venv/bin/python yf/yf_scan.py --codes`. If it won't connect, use the "Connecting" table in the README, one change at a time.
5. Then `--supported`, `--misfires`, `--honda-probe`. Save the output: it's the first real data on what this car answers. Record it in [yf/docs/honda-enhanced.md](../../../yf/docs/honda-enhanced.md) and the README's PID notes.

## Common asks

| They ask | Run | Then |
|---|---|---|
| "Any codes?" | `--codes` | Give each code, whether it's stored or pending, and which readiness monitors are incomplete |
| "Log my drive" | `--tag <word>`; Ctrl+C at the end | Remind them: Enter = marker, a word first labels it. Passenger works the laptop |
| "Chasing a noise" | `--set noise --honda --tag noise` | Fewer PIDs, faster rows. Ask them to mark every time they hear it, and to try to reproduce it at different gears and speeds |
| "Summarize that log" | `.venv/bin/python yf/tools/summarize.py yf/logs/<file>.csv` | Then read the CSV yourself for anything the summary doesn't cover |
| "Misfires?" | `--misfires` | Per-cylinder counts. Uneven counts on one cylinder are worth following up (plug, coil, injector) |

## Reading the numbers honestly

- Report what the data shows and what it can't. OBD sees the engine, not suspension. A clunk over bumps won't appear in any column.
- Fuel trims: compare banks and conditions (idle vs. cruise) rather than single values. Lean only at idle often means a vacuum leak; lean everywhere often means fuel delivery or the airflow reading. These are rules of thumb, not Honda specs.
- Anything marked "(candidate)" is an unconfirmed Honda decode. Don't draw conclusions from it until it's been cross-checked.
- Don't guess at a diagnosis. Say what to check next and why.

## Things that change the car: ask first

- `--clear-codes` resets readiness monitors and fuel trims. Ask, and remind them about emissions testing, before running it.
- Nothing in this repo runs actuator tests on the Pilot. Don't improvise Honda commands. Follow the sniff-and-replay rules in [yf/docs/honda-enhanced.md](../../../yf/docs/honda-enhanced.md).
