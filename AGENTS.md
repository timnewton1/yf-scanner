# For the agent

You are helping someone read a **2009–2015 Honda Pilot (YF3/YF4)** from a computer through an off-the-shelf ELM327 OBD2 adapter (the repo assumes an ELM327 v1.5 BLE mini). The person may be new to this. Go one step at a time, say what a thing is the first time you name it, and wait for them to report what they see.

Follow [.claude/skills/yf-scan/SKILL.md](.claude/skills/yf-scan/SKILL.md). Read [yf/README.md](yf/README.md) first.

Reference, read when a step needs it:

- [yf/docs/platforms.md](yf/docs/platforms.md): Linux, WSL2 (usbipd), Windows, macOS, Raspberry Pi
- [yf/docs/adapters.md](yf/docs/adapters.md): the reference adapter type, its quirks, transports, how the session works
- [yf/docs/honda-enhanced.md](yf/docs/honda-enhanced.md): what a 2011 Pilot answered, Honda-only reads, sniff-and-replay rules

## Rules

1. **Prove the computer side first.** Run `yf/yf_scan.py --port DEMO --report` before the car is involved.
2. **Reading is safe; clearing codes is not just reading.** `--clear-codes` resets readiness monitors and learned fuel trims. Ask first and mention emissions testing.
3. **No actuator tests, no writes.** Nothing here commands the car. `--query` only sends read services. Don't improvise Honda commands.
4. **Don't guess at a diagnosis.** Report what the data shows and what it can't. "(candidate)" decodes are unconfirmed.
5. Logs go to `yf/logs/`. Don't commit them unless asked. Don't put a VIN or an adapter's Bluetooth address in the repo. The adapter that last answered is saved in `.adapter` (git-ignored) and `--port auto`, the default, uses it, so commands and docs never need the address.
6. **Prefer scripts to command lists** for multi-step setup: `yf/setup/` has them, and new ones go there.

## Commands you will use

```sh
yf/setup/setup.sh                                    # Linux / WSL / Pi: venv, requirements, BlueZ
.venv/bin/python yf/yf_scan.py --port DEMO --report  # simulated Pilot
.venv/bin/python yf/tui.py --port DEMO --connect     # the same, as a terminal UI (tabs 1-7, q quits)
.venv/bin/python yf/yf_scan.py --adapters            # find the adapter (shows the saved one too)
.venv/bin/python yf/yf_scan.py --report              # key ON: health check (codes, readiness, misfires, Mode 06, live, Honda)
.venv/bin/python yf/yf_scan.py --tag baseline --honda   # log a drive; Enter drops a noise marker
.venv/bin/python yf/tools/summarize.py yf/logs/yf_XXXX.csv
.venv/bin/python yf/yf_scan.py --raw --query 0101 --module 1D   # one read-only request, every line shown
.venv/bin/python -m unittest discover -s yf/tests    # tests (no hardware)
```

Windows without WSL: `powershell -ExecutionPolicy Bypass -File yf\setup\windows.ps1 <same arguments>`.

## Working on the code

- `yf/tui.py` is a front end only: it calls `car.py`, `logger.py` and `tools/summarize.py` and holds one lock so a single request is on the adapter at a time. New reads go in `car.py`, not the TUI.
- `yf/elm/session.py` is the only code that speaks ELM327; `yf/car.py` is the only code that decides which module to ask. Both are documented in [yf/docs/adapters.md](yf/docs/adapters.md).
- The reference adapter returns only one reply to a broadcast when two modules answer. Address requests to one module (`to=`), never rely on a broadcast reply.
- Anything learned on the real car goes into `yf/sim/pilot.py` (so DEMO and the tests behave like the car) and into `yf/tests/fixtures/pilot_2011.py` as captured lines.
