# Honda-only data on the 2009–2015 Pilot

Standard OBD2 covers engine and emissions data. Transmission temperature, VCM (cylinder deactivation) state, VTM-4 and actuator tests are Honda's own, sent over the same port with requests Honda doesn't publish. This file collects what's known. **Nothing here is confirmed on a 2nd-gen Pilot yet.** Mark results as you confirm them.

## Candidate reads (Mode 22, read-only)

| Name | Request | Torque equation | Decoded as | Source | Status on YF |
|---|---|---|---|---|---|
| ATF temp | `22 2201` | `AA-40` (°C) | byte 27 after `62 22 01`, minus 40 | [vcmtuner Torque PID list](https://www.vcmtuner.com/pages/torque-pro-app-enhanced-honda-pids-for-vcmtuner-and-vcmtuner-ii) (tested on a 2022 Odyssey); Piloteers 3rd-gen owners | untested |
| VCM active cylinders | `22 2615` | `AY` | byte 51 after `62 26 15` | same list; a Piloteers post citing a Ridgeline list | untested |
| ECT 1 / 2 | `01 67` | `B-40` / `C-40` | standard PID 0x67 | same list | `--supported` will show it |

Reports on these strings (`unverified`, from Piloteers thread summaries):
- Header handling varies: some leave it blank (defaults to the engine computer, 7E0); others used `ATSP7` with `ATSH DA1DF1` (29-bit addressing), or `ATH0`. `yf_scan --honda-probe` asks 7E0 and 7E1 with 11-bit headers. If both stay silent, 29-bit addressing is the next thing to try.
- One owner got a constant −40 (a zero byte, so wrong address or offset). One said a cheap adapter returned the wrong packet for 2201.
- Torque Pro's built-in "transmission temp" on some Hondas just mirrors coolant temp ([BITOG](https://bobistheoilguy.com/forums/threads/honda-atf-temp-via-scan-tool.218312/)). Cross-check any ATF reading with a thermocouple on the cooler line.

Threads that likely hold exact strings (blocked to automated readers; read in a browser):
- https://www.piloteers.org/threads/monitor-honda-custom-obd2-pids-transmission-temp-etc.137202/
- https://www.piloteers.org/threads/transmission-temp-with-obd-scanner.149657/
- https://www.piloteers.org/threads/monitoring-automatic-transmission-fluid-temperature.78370/
- https://www.piloteers.org/threads/transmission-temperature-monitoring.155273/
- https://www.piloteers.org/threads/monitoring-vcm-cylinder-deactivation-using-an-obd2-scanner.179153/

## VCM and the ECO light

No standard PID shows VCM. Owners disagree on what the dash ECO light means (VCM active vs. just efficient driving), so it's a weak stand-in. The `22 2615` candidate is the best lead.

## Bus layout (expected, unverified)

- Engine data: CAN on pins 6/14, 11-bit IDs, 500 kbps, engine computer at 7E0 → 7E8. Expected from same-era Odysseys ([Carloop Odyssey findings](https://carloop.github.io/community/t/honda-odyssey-can-findings/410.html)), not measured on a Pilot.
- Pin 7 (K-line) is populated on some same-era Hondas, used by some non-engine modules. Which ones on the Pilot: unknown.
- Body CAN may not reach the port. Not covered by comma.ai's opendbc (which starts at the 2016 Pilot).

## Actuator tests: sniff and replay

Honda's tests (fuel pump, injectors, fans, VTM-4) go through the dealer tool's commands, which aren't published. The way in without guessing:

1. OBD Y-splitter on the port. A USB-CAN adapter (CANable / candleLight on SocketCAN) on one side records everything.
2. A real tool on the other side (a borrowed bidirectional scanner, a Foxwell NT510 Elite, or a day of Honda i-HDS) runs one test.
3. Decode the capture: which module address, which service, the start/stop bytes, any security-access exchange (service 27 seed/key).
4. Replay exactly the captured messages.

Rules:
- Parked, engine off unless the test needs it running.
- Replay only captured messages. **Never guess or scan control, routine, session-change, or memory-write services**: that is how modules get bricked.
- Scanning read-only identifiers (service 22) is low risk; do it parked.
