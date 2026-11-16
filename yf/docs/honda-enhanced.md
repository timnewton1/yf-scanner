# Honda-only data on the 2009–2015 Pilot

Standard OBD2 covers engine and emissions data. Transmission temperature, VCM (cylinder deactivation) state, VTM-4 and actuator tests are Honda's own, sent over the same port with requests Honda doesn't publish. This file collects what's known. **Nothing here is confirmed on a 2nd-gen Pilot yet.** Mark results as you confirm them.

## Candidate reads (Mode 22, read-only)

| Name | Request | Torque equation | Decoded as | Address that worked | Status on a 2nd-gen Pilot |
|---|---|---|---|---|---|
| ATF temp | `22 2201` | `AA-40` °C, written by owners as `AA*(9/5)-40` °F (same value) | byte 27 after `62 22 01`, minus 40 | 29-bit: `ATSP7` + `ATSH DA1DF1` | **reported working** on an '09 LX and two 2014s (forum reports, unverified) |
| VCM active cylinders | `22 2615` | `AY` | byte 51 after `62 26 15` | not stated | one poster, vehicle not stated (2016–2022 forum); no 2nd-gen report |
| ECT 1 / 2 | `01 67` | `B-40` / `C-40` | standard PID 0x67 | standard | `--supported` will show it |

`yf_scan.py --protocol 7 --honda-probe` sends these to `DA1DF1`. Without `--protocol 7` it tries 11-bit 7E0/7E1, which an '09 owner reported connecting but returning no ATF reading.

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
- OBD Fusion owners disagree on header: "ECM" worked for one; several others needed it blank.

Nobody quotes the service manual on the ATF sensor. One poster (no source): thermistor on a 5 V reference, 0.5–4.5 V normal, an open circuit reads −40. Sensor location is unknown. Cross-check against a thermocouple on the cooler line before trusting absolute numbers.

Other Honda reads: none posted for VTM-4, torque converter, gear, fan, oil life or A/F sensors. Sources that may have more: the Ridgeline Owners Club thread "Custom PIDs for ODB scantool/app like Torque or CarScanner" (source of `2615`), and an Odyclub thread "Transmission Temperature with Torque app and ELM327" (page 2, post #16).

## VCM and the ECO light

No standard PID shows VCM. 2nd-gen Pilots (2015 and earlier) have a dash ECO light; 2016+ don't. Owners disagree on what it means, and nobody states what triggers it on the 2nd gen, so it's a weak stand-in. The one `22 2615` report says VCM never cut cylinders below about 25 mph or at a stop, cycled often, and small throttle inputs brought all six back (vehicle not stated).

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
