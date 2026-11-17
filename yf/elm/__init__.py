"""ELM327 adapters on any transport.

Built around one adapter type, the ELM327 v1.5 BLE mini (adapter.REFERENCE: clone v1.5 firmware on
a PIC18F25K80, Bluetooth 5.x LE). Other ELM327s and transports go through the same code.

One layer for every way an ELM327 reaches the laptop. The ELM327 work itself (init, protocol
detection, OBD decoding) is python-OBD's; this package decides *how bytes get there* and hands
python-OBD a pyserial-compatible port:

    /dev/ttyUSB0, /dev/ttyACM0, COM3     USB adapters (pyserial, native)
    /dev/rfcomm0                         Bluetooth Classic (SPP) adapters, after `rfcomm bind`
    socket://192.168.0.10:35000          Wi-Fi adapters (pyserial's socket handler)
    ble://AA:BB:CC:DD:EE:FF              Bluetooth Low Energy adapters (ble.py, via bleak)

    adapter.py        transport-agnostic: port names, connect, identify the chip, list adapters
    ble.py            BLE transport: scan, GATT channel discovery, a blocking byte pipe
    protocol_ble.py   pyserial URL handler for ble://, so serial_for_url() (and python-OBD) can open it
"""

import serial

# serial_for_url("ble://...") imports <package>.protocol_ble
if __name__ not in serial.protocol_handler_packages:
    serial.protocol_handler_packages.append(__name__)

from .adapter import REFERENCE, Adapter, connect, find_adapters, identify, kind, normalize  # noqa: E402

__all__ = ["REFERENCE", "Adapter", "connect", "find_adapters", "identify", "kind", "normalize"]
