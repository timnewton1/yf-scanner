# Adapters: one ELM327 layer, any transport

The repo is built around one adapter type, the **ELM327 v1.5 BLE mini** (below). When a doc or a tool says "the adapter", it means that type. Other ELM327s and transports work through the same code, and the tool says when it sees something else.

`scanner/elm/` is the only code that knows how the adapter reaches the computer. `session.py` speaks the ELM327's command language over any pyserial-style port; adding a transport means adding a pyserial URL handler, nothing else.

| Adapter | `--port` | Transport | Notes |
|---|---|---|---|
| Bluetooth LE (the reference type, "works with iPhone") | `ble`, `ble:<address>` | `elm/ble.py` (bleak: BlueZ, WinRT, CoreBluetooth) | No pairing |
| Bluetooth Classic ("PIN 1234"; also the Classic side of a dual-mode mini) | `/dev/rfcomm0`, Windows: `COM5` | pyserial | Linux: pair, then `sudo rfcomm bind 0 <address>` |
| USB cable | `/dev/ttyUSB0`, `/dev/ttyACM0`, `COM3` | pyserial | Baud rate probed (38400, 9600, 115200, ...) or `--baud`. Linux: `dialout` / `uucp` group |
| Wi-Fi | `socket://192.168.0.10:35000` | pyserial's socket handler | Join the adapter's network first. Address and port vary by clone |
| Simulator | `DEMO` (= `sim://pilot`) | `elm/protocol_sim.py` | The reference adapter + a 2011 Pilot, in software |
| any of the above | `auto` | serial ports first, then a BLE scan | |

Two commands work the same on every transport:

```sh
.venv/bin/python scan.py --adapters                    # list what's plugged in or in range; opens nothing
.venv/bin/python scan.py --port <port> --adapter-info  # the chip, its voltage reading, and the link (key off is fine)
```

Every connection prints one `Adapter:` line ending in `type:`, measured against the reference type (`classify()` in `elm/adapter.py`):

| `type:` | What ATI/STI said | Meaning |
|---|---|---|
| `ELM327 v1.5 BLE mini` | `ELM327 v1.5` | The reference type, over any link (a dual-mode unit's Classic side counts too) |
| `STN` | STI names an STN chip (`STN2120 v5.6.19`) | OBDLink / vLinker class. Better than the reference; everything works |
| `v2.1 clone` | `ELM327 v2.1` | Usually a cut-down clone. Standard OBD2 fine; Honda reads may fail |
| `ELM327` | another version | Not the reference type; standard OBD2 should work |

A clone that answers `OK` to `STI` is not an STN: it answers OK to everything (see quirks below). The note line says so.

## The reference type: ELM327 v1.5 BLE mini

The generic thumb-sized "ELM327 Mini Bluetooth 5.1, for iPhone and Android" adapter, sold under many brands. Defined in code as `elm.REFERENCE`.

| | Measured on ours | Notes |
|---|---|---|
| Firmware | `ATI` → `ELM327 v1.5` | ELM Electronics never released a v1.5, so it's clone firmware. Sellers list a PIC18F25K80 chip ([example](https://www.ebay.com/itm/336643684168)); not opened up to check |
| `AT@1` | `OBDII to RS232 Adapter` | |
| Link | Bluetooth LE, advertises as `OBDII` | Whether ours also has a Classic side: not checked |
| GATT | service `FFF0`: notify `FFF1`, write `FFF2` (write with response), MTU 248 | Also a second pipe, service `AE00` (`AE01` write-without-response, `AE02` notify), unused: the `FFF0` pair is the one wired to the ELM chip |
| Speed | about 0.15 s per OBD request, 0.06 s per AT command | Six PIDs fit in one request, so that's about 40 PIDs/s |
| Phone app | YMOBD, Car Scanner and similar | YMOBD ([App Store](https://apps.apple.com/us/app/ymobd/id1627949735)) is sold bundled with these |

### Quirks (all handled in `elm/session.py`, all modeled in `sim/elm.py`)

1. **Answers `OK` to commands it doesn't implement.** `STI`, `STDI`, `ATPPS`, `AT@2`, `ATCS` all came back `OK`. So an `OK` doesn't prove a setting took, and feature probing by asking doesn't work.
2. **Returns only one reply to a broadcast** when two modules answer. On the Pilot, a functional `0100` (to all modules) usually came back with only the ECM's reply or only the TCM's, and which one varied. Changing `ATST`/`ATAT` timing didn't fix it (`ATST FF` made it always the TCM). The session sends every request to one module by its physical address instead; broadcasts are only used to find modules.
3. **Drops a BLE frame now and then.** About one in five long multi-frame replies (6–9 frames) lost one consecutive frame. The session checks ISO-TP sequence numbers and retries (twice by default), so a broken reply is never decoded. The log summary reports how many requests were retried.
4. **The first link sometimes drops right after connecting.** `connect()` reopens once.

What it means here:
- **Standard OBD2:** fine, both modules, as long as requests are addressed per module (they are).
- **Honda 29-bit reads:** fine. `22 2201` and `22 2615` got correctly echoed replies. The "answers the wrong request" fault some clones have didn't show up.
- **One connection at a time.** Close YMOBD (or any phone app) first, or the laptop won't see it.
- **The Pilot's OBD port is always powered** (pin 16 is on battery power), so the adapter stays lit with the key off. Unplug it if the car will sit for more than a few days. Standby current not measured.

## Our unit: ViaBecs OBD-HT

Amazon listings ([US](https://www.amazon.com/ViaBecs-Diagnostic-Professional-Bluetooth-Supports/dp/B0CCYKZ8YF), [US mini](https://www.amazon.com/ViaBecs-Bluetooth-Wireless-Diagnostic-Compatible/dp/B0CCYN9W9N), [OBD-HT](https://www.amazon.com/Scanner-Bluetooth-Adapter-Supports-Protocols/dp/B0H2M17JNG)): Bluetooth 5.1, iPhone and Android, the YMOBD app.

### First contact, 2026-11-16 (2011 Pilot 4WD, engine idling)

| Check | Result |
|---|---|
| `--adapters` | `OBDII`, flagged likely by name; rssi -54 to -57 dBm from the driver's seat |
| `--adapter-info` | `ATI` ELM327 v1.5, `AT@1` OBDII to RS232 Adapter, `STI` OK (not an STN; quirk 1), `ATRV` 13.8–13.9 V (engine running) |
| `type:` | `ELM327 v1.5 BLE mini`: **it is the reference type** |
| GATT | `FFF0` / `FFF1` / `FFF2`, as expected (table above) |
| Classic side | not checked |
| Protocol | auto-detect → `A7`, ISO 15765-4 CAN 29-bit 500k |
| Rate | `drive` set (18 PIDs, three requests): 0.5 s per read; with `--honda`, about 1 row/s |

Reached from WSL2 (usbipd + BlueZ LE-only) and, for the first run, from Windows Python with bleak. See [platforms.md](platforms.md).

## How the session works (`elm/session.py`)

Setup: `ATZ`, then `ATE0 ATL0 ATS0 ATH1 ATAT1` (no echo, no linefeeds, no spaces, headers on, adaptive timing), then `ATSP0` and a `0100` to find the protocol, read back with `ATDPN`. With `--protocol N` it only counts as connected if the car answers on that protocol.

Requests: `request(data, to=module)` sets `ATSH DA<module>F1` on 29-bit (`7E0`-style on 11-bit) only when the target changes, sends the request, and reads up to the `>` prompt. With headers on, every reply line starts with the sender (`18 DA F1 11` = from module 11). Lines are grouped by sender and reassembled as ISO-TP: single frames, or a first frame plus numbered consecutive frames. A gap in the numbering marks that module's reply broken, and it's retried. `7F xx 78` (response pending) is skipped for the real answer; other `7F` refusals come back to the caller with their code.

Mode 01 with several PIDs (`01 0C 0D 05 04 06 07`) gets one reply with each PID followed by its data; `obd2/pids.py` knows every PID's length to split it.

Everything else the scanner knows comes from these replies. There's no other dependency between the adapter and the car code.

## How the BLE transport works (`elm/ble.py`)

A BLE adapter carries the ELM327's serial stream over two GATT characteristics: one it *notifies* on (adapter to computer) and one the computer *writes* to. `elm/ble.py` picks the pair this way:

1. `ble:<address>?notify=<uuid>&write=<uuid>` if given (for a clone nothing else finds).
2. Known layouts, in order: `FFF0` (FFF1 notify / FFF2 write, the reference type), `FFE0` (FFE1 both, HM-10 style), `18F0` (2AF0 / 2AF1), Vgate/vLinker, Microchip transparent UART, Nordic UART. Collected from open-source OBD apps.
3. Any other service with a notify + write pair, skipping standard ones (GAP, GATT, device info, battery).

If more than one pair qualifies, it sends `ATI` (read-only) down each and keeps the first that answers. Some clones expose two pipes and wire only one to the chip.

Writes use "without response" when the characteristic allows it, as phone apps do, chunked to the link's packet size. Replies arrive as notifications, often in 20-byte pieces, and are buffered until the session reads up to the `>` prompt. If the link drops, the next read or write raises at once (no hang), and the logger reconnects.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

- `test_decode.py`: the reply parser and decoders against replies captured from the real car (`tests/fixtures/pilot_2011.py`), including one with a dropped frame.
- `test_car.py`: the car layer, CLI and logger against the simulator, with its quirks on: one reply per broadcast, OK to anything, dropped frames, a link that drops mid-log, an engine that stops.
- `test_elm.py`: the transports. A fake BLE adapter (including one with two pipes where only the second is wired, and a link that drops), and a pty standing in for USB (with the baud probe).

The simulator behaves like the reference type in front of a 2011 Pilot. The tests prove the code; real-adapter behavior goes under "First contact" above.
