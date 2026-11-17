# Honda-only data on the 2009–2015 Pilot

Standard OBD2 covers engine and emissions data. Transmission temperature, VCM (cylinder deactivation) state, VTM-4 and actuator tests are Honda's own, sent over the same port with requests Honda doesn't publish. This file collects what's known and what our 2011 Pilot actually answered. Mark results as you confirm them.

## What a 2011 Pilot answered (measured, 2026-11-16)

2011 Pilot 4WD (YF4), engine idling, warm. Reference adapter (ELM327 v1.5 BLE mini) over BLE. Every request below is read-only.

**Bus:** ISO 15765-4 CAN, **29-bit, 500 kbps**. Auto-detect picks it (`ATDPN` → `A7`). With `ATSP6` (11-bit), `0100` to `7DF`, `7E0` and `7E1` all got `NO DATA`. So the "11-bit, 7E0/7E8" expectation from same-era Odysseys doesn't hold for this Pilot.

**Modules answering OBD requests** (physical address `DA<xx>F1`, replies from `18 DA F1 <xx>`):

| Addr | Mode 09 `0A` name | Cal ID (`09 04`) | Mode 01 | Mode 06 | Mode 09 | Readiness (`01 01`) |
|---|---|---|---|---|---|---|
| `11` | `ECM-EngineControl` | `37805-RN0-A830` | 42 PIDs: `00` `BFBFA893`, `20` `801FF119`, `40` `FAD00000` | yes (O2 B1S1/B1S2/B2S1/B2S2, catalyst B1/B2, EGR, EVAP .090"/.020", purge, misfire cyl 1–6) | `02 04 06 08 0A` (VIN, cal ID, CVN, in-use counters, name) | `00 07 E5 00`: all 8 monitors supported and complete |
| `1D` | `TCM-TransmisCtrl` | `37806-RN0-A820` | 18 PIDs: `00` `983A8003`, `20` `8000A001`, `40` `CAC00000` | no | `04 06 0A` | `00 04 00 00`: components only, complete |

No other address answered `01 00` (tried `10 18 1A 1E 28 30`; `--modules --sweep` tries all 256). VCM, VTM-4 and body modules may well be there and just not answer OBD Mode 01.

Codes: Mode `03`, `07` and `0A` to both modules → `43 00`, `47 00`, `4A 00`: no stored, pending or permanent codes. O2 sensors (PID `13` = `33`): B1S1, B1S2, B2S1, B2S2. Upstream are wideband (PIDs `34` and `38` = sensors 1 and 5). OBD standard (PID `1C`): `01`, CARB OBD-II.

**Honda Mode 22:**

| Request | To `11` (ECM) | To `1D` (TCM) |
|---|---|---|
| `22 2201` (ATF temp candidate) | `7F 22 31` (request out of range) | **36 data bytes**: `FF FF FF 80 00 00 00 02 BF 00 00 02 9F 08 23 50 00 32 19 27 79 28 17 89 5C 4E 67 8B 00 00 00 00 00 00 00 00` |
| `22 2615` (VCM candidate) | **54 data bytes**, all `00` except `F8` at index 5 and `77 00 03 00 06` at 46–50 | `7F 22 31` |

- **ATF, byte AA (index 26):** read `0x64` → `0x65` → `0x67` → `0x6F` (140 → 141.8 → 145.4 → 159.8 °F) over about 20 minutes of idling. That's the expected direction and range for ATF warming at idle. Index 20 read `0x79`–`0x7B` (81–83 °C) at the same time as coolant PID `05` read `0x79`: index 20 looks like coolant temperature, which supports the "minus 40 °C" encoding for this block. **Not yet cross-checked against a thermometer.**
- **VCM, byte AY (index 50):** read `06` at idle, every time. VCM never deactivates at idle, so that's consistent, but it doesn't confirm the decode. Needed: a log on a light steady cruise (35–60 mph), where it should drop to `03`, with the dash ECO light as a cross-check.

## Candidate reads (Mode 22, read-only)

| Name | Request | Module | Torque equation | Decoded as | Status on a 2nd-gen Pilot |
|---|---|---|---|---|---|
| ATF temp | `22 2201` | `1D` TCM (`ATSH DA1DF1`) | `AA-40` °C, written by owners as `AA*(9/5)-40` °F (same value) | byte 27 after `62 22 01`, minus 40 | **answers on our 2011**, plausible values; also reported by owners of an '09 LX and two 2014s |
| VCM active cylinders | `22 2615` | `11` ECM (`ATSH DA11F1`) | `AY` | byte 51 after `62 26 15` | **answers on our 2011**, reads 6 at idle; cruise not yet seen |
| ECT 1 / 2 | `01 67` | | `B-40` / `C-40` | standard PID 0x67 | not supported on our 2011 (PID 67 not in the mask) |

`scan.py --honda-probe` sends each to its module and prints the raw bytes. `--honda` adds both as log columns.

### What owners reported (Piloteers, owner posts, unverified)

ATF temp, 2nd-gen Pilots:
- **'09 LX, Torque Pro + Veepeak (2018, 2021):** `222201`, `AA*(9/5)-40`, −40 to 300 °F. Init string `ath0` worked at first, later stopped; `atsp7\natshda1df1` then worked. With no init string the ECU connected but no ATF value came back. Torque and Car Scanner matched exactly; cold ATF matched coolant within 1 °C.
- **2014 Touring, Engine Link + WiFi ELM327 (2015):** same PID and equation, "confirmed"; ATF settled around 140 °F.
- **2014 EX-L 4WD, OBD Fusion + Foseal WiFi (2018):** mode `22`, PID `2201`, header "ECM", `(AA*(9/5)-40)`.
- **2011 EX-L and a '15, ScanGauge II (2015, 2020):** XGauge `TXD 1DF1222201`, `RXF 032400000000`, `RXD 2808`, `MTH 00090005FFD8`. "Should work on '07 and above." 190–230 °F towing in the desert.
- **Car Scanner ELM OBD2 (free app):** has a built-in "ATF Temp F" with the "Honda OBD-II/EOBD + AT (CAN)" profile.

Also reported working on: 2016–2020 Pilots, 2014 Ridgeline, 2012 Crosstour, 2008 Civic Hybrid, 2012 Civic.

Failures:
- Constant −40 / −41.8 (a zero byte: wrong address or no data). One owner fixed it by switching from a cheap adapter to a BAFX.
- **Cheap clones answering the wrong request:** a 2014 Ridgeline owner's clone returned the `2200` packet for `2201`, but was fine on standard PIDs. A VGate iCar Pro worked. The probe checks the `62 22 01` echo, so this shows up as "answered the wrong request".
- Adapters reported working: Veepeak, VGate iCar Pro / Icar2, OBDLink MX, BAFX, Foseal WiFi. Failed or suspect: cheap Wish clones, Kobra.
- The repo's adapter type, the ELM327 v1.5 BLE mini ([adapters.md](adapters.md)): **works** on our 2011. Both reads came back with the right `62 xx xx` echo. It drops a frame from long replies now and then; the session retries those.
- OBD Fusion owners disagree on header: "ECM" worked for one; several others needed it blank.

Nobody quotes the service manual on the ATF sensor. One poster (no source): thermistor on a 5 V reference, 0.5–4.5 V normal, an open circuit reads −40. Sensor location is unknown. Cross-check against a thermocouple on the cooler line before trusting absolute numbers.

Other Honda reads: none posted for VTM-4, torque converter, gear, fan, oil life or A/F sensors. Sources that may have more: the Ridgeline Owners Club thread "Custom PIDs for ODB scantool/app like Torque or CarScanner" (source of `2615`), and an Odyclub thread "Transmission Temperature with Torque app and ELM327" (page 2, post #16).

## VCM and the ECO light

No standard PID shows VCM. 2nd-gen Pilots (2015 and earlier) have a dash ECO light; 2016+ don't. Owners disagree on what it means, and nobody states what triggers it on the 2nd gen, so it's a weak stand-in. The one `22 2615` report says VCM never cut cylinders below about 25 mph or at a stop, cycled often, and small throttle inputs brought all six back (vehicle not stated).

## Bus layout

- **Measured (2011):** OBD on CAN pins 6/14, **29-bit IDs, 500 kbps**; ECM at `11`, TCM at `1D`, tester `F1`. See the top of this file.
- The ECM and TCM may be two logical modules in one Honda PCM box: their part numbers differ (`37805-RN0`, `37806-RN0`) but share the `RN0` code. Not checked.
- Earlier expectation, now known wrong for this car: 11-bit IDs with the engine at 7E0 → 7E8, from same-era Odysseys ([Carloop Odyssey findings](https://carloop.github.io/community/t/honda-odyssey-can-findings/410.html)). Other model years may differ; the scanner handles both.
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
