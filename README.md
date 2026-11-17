# yf-scanner

Read a **2009–2015 Honda Pilot (YF3/YF4)** from a laptop over OBD2, and let an AI agent do the reading with you.

An off-the-shelf ELM327 adapter (the repo assumes an ELM327 v1.5 BLE mini, a thumb-sized Bluetooth dongle) plugs into the OBD port under the dash. A small Python scanner with its own ELM327 driver reads both the engine and transmission computers. No wiring, no tools. Runs on Linux, WSL2, Windows and macOS, and on a Raspberry Pi that logs every drive by itself.

Goals: find out what's behind noises and rough running, and catch slow drift (fuel trims, misfires, warm-up, charging, transmission temperature) before it gets expensive.

## The easy way

1. Clone this repo and open it in Claude Code, Codex or another coding agent.
2. Say: **"Help me hook up my ELM adapter to my Pilot."**

The agent reads [AGENTS.md](AGENTS.md) and walks you through it one step at a time.

## By hand

Everything lives in [`yf/`](yf/README.md); its README has the full command list and connection troubleshooting.

```sh
yf/setup/setup.sh                                     # Linux / WSL / Pi (Windows: yf\setup\windows.ps1)
.venv/bin/python yf/yf_scan.py --port DEMO --report   # simulated Pilot: check the laptop side first
.venv/bin/python yf/yf_scan.py --adapters             # find the adapter
.venv/bin/python yf/yf_scan.py --port ble:<address> --report   # key ON: health check
```

## Credits and license

Forked from [MZULALI/xj-scanner](https://github.com/MZULALI/xj-scanner), a scanner for 1991–95 Jeeps; the approach (a small Python tool, plain CSV logs, an agent that reads them with you) comes from there. GPLv3.

This is a hobby project. It talks to your car's computers; you use it at your own risk.
