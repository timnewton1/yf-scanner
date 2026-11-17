# For the agent

You are helping someone read a **2009–2015 Honda Pilot (YF3/YF4)** from a computer through an off-the-shelf ELM327 OBD2 adapter (the repo assumes an ELM327 v1.5 BLE mini). The person may be new to this. Go one step at a time, say what a thing is the first time you name it, and wait for them to report what they see.

Follow [.claude/skills/yf-scan/SKILL.md](.claude/skills/yf-scan/SKILL.md). Read [README.md](README.md) first.

Reference, read when a step needs it:

- [docs/platforms.md](docs/platforms.md): Linux, WSL2 (usbipd), Windows, macOS, Raspberry Pi
- [docs/adapters.md](docs/adapters.md): the reference adapter type, its quirks, transports, how the session works
- [docs/honda-enhanced.md](docs/honda-enhanced.md): what a 2011 Pilot answered, Honda-only reads, sniff-and-replay rules

## Rules

1. **Prove the computer side first.** Run `scan.py --port DEMO --report` before the car is involved.
2. **Reading is safe; clearing codes is not just reading.** `--clear-codes` resets readiness monitors and learned fuel trims. Ask first and mention emissions testing.
3. **No actuator tests, no writes.** Nothing here commands the car. `--query` only sends read services. Don't improvise Honda commands.
4. **Don't guess at a diagnosis.** Report what the data shows and what it can't. "(candidate)" decodes are unconfirmed.
5. Logs go to `logs/`. Don't commit them unless asked. Don't put a VIN or an adapter's Bluetooth address in the repo. The adapter that last answered is saved in `.adapter` (git-ignored) and `--port auto`, the default, uses it, so commands and docs never need the address.
6. **Prefer scripts to command lists** for multi-step setup: `setup/` has them, and new ones go there.

## Commands you will use

```sh
setup/setup.sh                                           # Linux / WSL / Pi: uv, .venv (uv sync), BlueZ
.venv/bin/python scan.py --port DEMO --report            # simulated Pilot
.venv/bin/python tui.py --port DEMO --connect            # the same, as a terminal UI (tabs 1-7, q quits)
.venv/bin/python scan.py --adapters                      # find the adapter (shows the saved one too)
.venv/bin/python scan.py --report                        # key ON: health check (codes, readiness, misfires, Mode 06, live, Honda)
.venv/bin/python scan.py --tag baseline --honda          # log a drive; Enter drops a noise marker
.venv/bin/python summarize.py logs/yf_XXXX.csv
.venv/bin/python scan.py --raw --query 0101 --module 1D  # one read-only request, every line shown
.venv/bin/python -m unittest discover -s tests           # tests (no hardware)
```

Windows without WSL: `powershell -ExecutionPolicy Bypass -File setup\windows.ps1 <same arguments>`.

## Working on the code

- Dependencies live in `pyproject.toml` and `uv.lock`, managed by [uv](https://docs.astral.sh/uv/): `uv add <pkg>` to add one, `uv sync` to update `.venv`. Commit `uv.lock` with the change. `uv run python ...` works anywhere `.venv/bin/python ...` does.

- `tui.py` is a front end only: it calls `scanner/car.py`, `scanner/logger.py` and `summarize.py` and holds one lock so a single request is on the adapter at a time. New reads go in `scanner/car.py`, not the TUI.
- `scanner/elm/session.py` is the only code that speaks ELM327; `scanner/car.py` is the only code that decides which module to ask. Both are documented in [docs/adapters.md](docs/adapters.md).
- The reference adapter returns only one reply to a broadcast when two modules answer. Address requests to one module (`to=`), never rely on a broadcast reply.
- Anything learned on the real car goes into `scanner/sim/pilot.py` (so DEMO and the tests behave like the car) and into `tests/fixtures/pilot_2011.py` as captured lines.
