"""ELM327 adapters on any transport, and the session that talks through them.

Built around one adapter type, the ELM327 v1.5 BLE mini (adapter.REFERENCE: clone v1.5 firmware,
Bluetooth LE). Other ELM327s and transports go through the same code.

    /dev/ttyUSB0, /dev/ttyACM0, COM3     USB adapters (pyserial, baud rate probed)
    /dev/rfcomm0, COM5                   Bluetooth Classic (SPP) adapters
    socket://192.168.0.10:35000          Wi-Fi adapters (pyserial's socket handler)
    ble://AA:BB:CC:DD:EE:FF              Bluetooth Low Energy adapters (ble.py, via bleak)
    sim://pilot                          the simulated adapter + 2011 Pilot (--port DEMO)

    session.py        the ELM327 dialogue: AT commands, requests per module, ISO-TP reassembly
    adapter.py        transport-agnostic: port names, open, identify the chip, list adapters
    ble.py            BLE transport: scan, GATT channel discovery, a blocking byte pipe
    protocol_ble.py   pyserial URL handler for ble://
    protocol_sim.py   pyserial URL handler for sim://
"""

import serial

# serial_for_url("ble://...") imports <package>.protocol_ble, "sim://" protocol_sim
if __name__ not in serial.protocol_handler_packages:
    serial.protocol_handler_packages.append(__name__)

from .adapter import REFERENCE, Adapter, NotConnected, connect, find_adapters, identify, kind, normalize  # noqa: E402
from .session import Elm, ElmError, LinkLost, Reply  # noqa: E402

__all__ = ["REFERENCE", "Adapter", "NotConnected", "connect", "find_adapters", "identify", "kind", "normalize",
           "Elm", "ElmError", "LinkLost", "Reply"]
