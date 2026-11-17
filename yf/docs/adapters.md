# Adapters: one ELM327 layer, any transport

The repo is built around one adapter type, the **ELM327 v1.5 BLE mini** (below). When a doc or a tool says "the adapter", it means that type. Other ELM327s and transports work through the same code, and the tool says when it sees something else.

`yf/elm/` is the only code that knows how the adapter reaches the laptop. Everything above it (`yf_scan.py`, `honda.py`, python-OBD itself) sees the same pyserial-style port whatever the transport. Adding a transport means adding a pyserial URL handler, nothing else.

| Adapter | `--port` | Transport | Notes |
|---|---|---|---|
| Bluetooth LE (the reference type, "works with iPhone") | `ble`, `ble:<address>` | `elm/ble.py` (bleak, BlueZ) | No pairing, no rfcomm |
| Bluetooth Classic ("PIN 1234"; also the Classic side of a dual-mode mini) | `/dev/rfcomm0` | pyserial over rfcomm | Pair, then `sudo rfcomm bind 0 <address>` |
| USB cable | `/dev/ttyUSB0`, `/dev/ttyACM0`, `COM3` | pyserial | Baud rate auto-detected. Needs the `dialout` (or `uucp`) group on Linux |
| Wi-Fi | `socket://192.168.0.10:35000` | pyserial's socket handler | Join the adapter's network first. Address and port vary by clone |
| any of the above | `auto` | serial ports first, then a BLE scan | |

Two commands work the same on every transport:

```sh
.venv/bin/python yf/yf_scan.py --adapters                    # list what's plugged in or in range; opens nothing
.venv/bin/python yf/yf_scan.py --port <port> --adapter-info  # the chip, its voltage reading, and the link (key off is fine)
```

Every connection prints one `Adapter:` line ending in `type:`, measured against the reference type (`classify()` in `elm/adapter.py`):

| `type:` | What ATI/STI said | Meaning |
|---|---|---|
| `ELM327 v1.5 BLE mini` | `ELM327 v1.5` | The reference type, over any link (a dual-mode unit's Classic side counts too) |
| `STN` | STI answers | OBDLink / vLinker class. Better than the reference; everything works |
| `v2.1 clone` | `ELM327 v2.1` | Usually a cut-down clone. Standard OBD2 fine; expect Honda reads to fail |
| `ELM327` | another version | Not the reference type; standard OBD2 should work |

## The reference type: ELM327 v1.5 BLE mini

The generic thumb-sized "ELM327 Mini Bluetooth 5.1, for iPhone and Android" adapter, sold under many brands. Defined in code as `elm.REFERENCE`.

| | Expected | Why we think so |
|---|---|---|
| Firmware | `ATI` → `ELM327 v1.5` | Sellers of this type list v1.5 ([example](https://www.ebay.com/itm/336643684168), [example](https://www.felkodslasare.se/en-en/products/super-mini-elm327-obdii-v15-diagnostikverktyg)). ELM Electronics never released a v1.5, so it's clone firmware |
| Chip | PIC18F25K80 plus a separate Bluetooth module, two boards | Same listings. On this chip, v1.5 is reputed to be the better class of clone, with roughly the full command set; cheap v2.1 minis are reputed to be cut down ([elm327mini.com](https://elm327mini.com/), an affiliate site). Not verified by us |
| Link | Bluetooth 5.x LE. Many units are dual-mode and also offer Classic SPP with PIN 1234 | iPhone apps can only reach BLE (or Wi-Fi) adapters. Dual-mode units are sold as such ([example](https://www.sunsky-online.com/p/CAR9854/Dual-Mode-Bluetooth-5.1-ELM327-V1.5-OBD2-Car-Fault-Diagnostic-Tool-PIC18F25K80-Chip.htm)) |
| GATT | service `FFF0`, notify `FFF1`, write `FFF2` | The usual layout for BLE ELM327 minis in open-source OBD apps. Not confirmed for this type |
| Phone app | YMOBD, Car Scanner and similar | YMOBD ([App Store](https://apps.apple.com/us/app/ymobd/id1627949735)) is sold bundled with "ELM327 Mini Bluetooth 5.1" units |

What it means here:

- **Standard OBD2:** expected to be fine.
- **Honda 29-bit reads** (`ATSP7`, `ATSH DA1DF1`): this is where clones differ. Owners report some cheap clones answering the wrong request ([honda-enhanced.md](honda-enhanced.md)). Whether this type does is unknown; `--honda-probe` checks the echo and will say.
- **One connection at a time.** Close YMOBD (or any phone app) first, or the laptop won't see it.
- **The Pilot's OBD port is always powered** (pin 16 is on battery power), so the adapter stays lit with the key off. Unplug it if the car will sit for more than a few days. Standby current not measured.

## Our unit: ViaBecs OBD-HT

The Amazon listings ([US](https://www.amazon.com/ViaBecs-Diagnostic-Professional-Bluetooth-Supports/dp/B0CCYKZ8YF), [US mini](https://www.amazon.com/ViaBecs-Bluetooth-Wireless-Diagnostic-Compatible/dp/B0CCYN9W9N), [OBD-HT](https://www.amazon.com/Scanner-Bluetooth-Adapter-Supports-Protocols/dp/B0H2M17JNG)) say: Bluetooth 5.1, iPhone and Android, the YMOBD app, model OBD-HT, 9 OBD2 protocols, and pick "Bluetooth LE, Bluetooth 4.0 or classic Bluetooth" in your app. That makes it the reference type: BLE, probably dual-mode. The listings don't name the chip or firmware. The first `--adapter-info` will confirm it (`type: ELM327 v1.5 BLE mini`) or not. If not, update `elm.REFERENCE` and this file to what it actually is.

### First contact, to record here

1. Plug it into the car (key off is fine). Laptop Bluetooth on: `systemctl start bluetooth`.
2. `--adapters`. Write down the name and address it shows. If it isn't flagged as likely, its advertised name doesn't match the list in `elm/ble.py` (`LIKELY_NAME`); add the name there.
3. `--port ble:<address> --adapter-info`. Paste the output into this file: the ATI version, AT@1, STI, the `type:`, and which GATT service/characteristics carried the data.
4. If it also showed up as a Classic device (PIN 1234), note that too.
5. Key ON: `--port ble:<address> --codes`, then the rest of the first-run list in the README.
6. Log 60 s at idle (`--seconds 60 --tag ble-rate`) and note the rows/s it prints. BLE adds latency per request, so the rate is worth knowing before a noise hunt.

Recorded results: *(none yet)*

## How the BLE transport works

A BLE adapter carries the ELM327's serial stream over two GATT characteristics: one it *notifies* on (adapter to laptop) and one the laptop *writes* to. `elm/ble.py` picks the pair this way:

1. `ble:<address>?notify=<uuid>&write=<uuid>` if given (for a clone nothing else finds).
2. Known layouts, in order: `FFF0` (FFF1 notify / FFF2 write, the reference type's expected layout), `FFE0` (FFE1 both, HM-10 style), `18F0` (2AF0 / 2AF1), Vgate/vLinker, Microchip transparent UART, Nordic UART. Collected from open-source OBD apps, not measured here.
3. Any other service with a notify + write pair, skipping standard ones (GAP, GATT, device info, battery).

If more than one pair qualifies, it sends `ATI` (read-only) down each and keeps the first that answers. Some clones expose two pipes and wire only one to the chip.

Writes go out without response when the characteristic allows it, as phone apps do, chunked to the link's packet size. Replies arrive as notifications, often in 20-byte pieces, and are buffered until python-OBD reads up to the `>` prompt. If the link drops, the read fails right away instead of hanging, and python-OBD reports the adapter as disconnected.

## Tests

```sh
.venv/bin/python -m unittest discover yf/tests -v
```

`tests/elm_sim.py` is a byte-level ELM327. The tests run python-OBD through the real `elm/` code against it twice: over a fake BLE adapter and over a pty standing in for USB. The fake BLE cases include one with two pipes where only the second is wired, and a link that drops mid-drive. The simulator behaves like the reference type (v1.5 firmware, FFF0 pipe, 20-byte notifications). The tests prove the layer, not the ViaBecs: real-adapter behavior goes under "Recorded results" above.
