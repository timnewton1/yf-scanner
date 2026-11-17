# For the agent

You are helping someone read a **2009–2015 Honda Pilot (YF3/YF4)** from a laptop through an off-the-shelf ELM327 OBD2 adapter (the repo assumes an ELM327 v1.5 BLE mini). The person may be new to this. Go one step at a time, say what a thing is the first time you name it, and wait for them to report what they see.

Follow [.claude/skills/yf-scan/SKILL.md](.claude/skills/yf-scan/SKILL.md). Read [yf/README.md](yf/README.md) first.

Reference, read when a step needs it:

- [yf/docs/adapters.md](yf/docs/adapters.md): the reference adapter type, transports, first-contact checklist
- [yf/docs/honda-enhanced.md](yf/docs/honda-enhanced.md): Honda-only data, 29-bit addressing, sniff-and-replay rules

## Rules

1. **Prove the laptop side first.** Run `yf/yf_scan.py --port DEMO --codes` before the car is involved.
2. **Reading is safe; clearing codes is not just reading.** `--clear-codes` resets readiness monitors and learned fuel trims. Ask first and mention emissions testing.
3. **No actuator tests.** Nothing here commands the car. Don't improvise Honda commands.
4. **Don't guess at a diagnosis.** Report what the data shows and what it can't. "(candidate)" decodes are unconfirmed.
5. Logs go to `yf/logs/`. Don't commit them unless asked.

## Commands you will use

```sh
python3 -m venv .venv && .venv/bin/pip install -r yf/requirements.txt
.venv/bin/python yf/yf_scan.py --port DEMO --codes   # simulated Pilot
.venv/bin/python yf/yf_scan.py --adapters            # find the adapter
.venv/bin/python yf/yf_scan.py --codes               # key ON: codes and readiness
.venv/bin/python yf/yf_scan.py --tag baseline        # log a drive; Enter drops a noise marker
.venv/bin/python yf/tools/summarize.py yf/logs/yf_XXXX.csv
.venv/bin/python -m unittest discover -s yf/tests    # tests
```
