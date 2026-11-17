"""Transport-agnostic ELM327 handling: name the port, connect through python-OBD, identify the chip.

Everything here is the same for USB, Bluetooth Classic, Wi-Fi and BLE adapters. The only
per-transport choices are the URL python-OBD opens and whether to auto-detect a baud rate
(only a real UART has one).
"""

import glob
import os

import obd
from serial.tools import list_ports

from . import ble

# The adapter type this repo is built around. Every tool, test and doc assumes this unless the
# adapter says otherwise (see classify()). Inferred from listings for the unit we use (a ViaBecs
# "OBD-HT", sold for iPhone with the YMOBD app) and the generic "ELM327 Mini Bluetooth 5.1" units
# sold with the same app; not yet confirmed by an ATI read. See docs/adapters.md.
REFERENCE = {
    "name": "ELM327 v1.5 BLE mini",
    "firmware": "reports 'ELM327 v1.5' to ATI: clone firmware, ELM Electronics never released a v1.5",
    "chip": "PIC18F25K80 microcontroller plus a separate Bluetooth module (two-board build)",
    "link": "Bluetooth 5.x LE (what iPhone apps use); many units also offer Classic SPP, PIN 1234",
    "gatt": "service FFF0: notify FFF1, write FFF2 (expected, the usual layout for this type)",
    "examples": ["ViaBecs OBD-HT (ours)"],
}

# USB-serial bridges ELM327 clones are built on, by USB vendor:product.
USB_BRIDGES = {
    "1a86:7523": "CH340", "1a86:5523": "CH341", "0403:6001": "FTDI FT232", "0403:6015": "FTDI FT231X",
    "10c4:ea60": "CP210x", "067b:2303": "PL2303",
}


PERMISSION_HELP = ("Add yourself to the group that owns it (ls -l shows it: usually 'dialout', 'uucp' on some "
                   "distros): sudo usermod -aG <group> $USER, then log out and in.")


class NotConnected(Exception):
    """No ELM327 answered on the given port."""


class Adapter:
    def __init__(self, url, conn, link=None):
        self.url, self.conn, self.link = url, conn, link
        self.kind = kind(url)
        self.ident = {}



# --- naming ports ---------------------------------------------------------------

def normalize(port):
    """User-facing port name -> what serial_for_url opens.
    'ble' (scan) -> 'ble://', 'ble:AA:BB:..' -> 'ble://AA:BB:..'; everything else passes through:
    a device path, a COM port, or any pyserial URL (socket://host:port for Wi-Fi adapters)."""
    p = port.strip()
    low = p.lower()
    if low == "ble":
        return "ble://"
    if low.startswith("ble:") and not low.startswith("ble://"):
        return "ble://" + p[4:]
    return p


def kind(url):
    low = url.lower()
    if low.startswith("ble://"):
        return "BLE"
    if low.startswith("socket://"):
        return "Wi-Fi"
    if "rfcomm" in low:
        return "Bluetooth Classic"
    return "USB/serial"


# --- connecting -----------------------------------------------------------------

def _open(url, timeout, fast, check_voltage, protocol):
    # A UART adapter's baud rate is unknown, so python-OBD probes for it. Radio and network
    # links have no baud rate; give python-OBD one so it skips the probe.
    baud = None if kind(url) in ("USB/serial", "Bluetooth Classic") else 38400
    ble.last_link = ble.last_error = None
    conn = obd.OBD(url, baudrate=baud, fast=fast, timeout=timeout, check_voltage=check_voltage, protocol=protocol)
    return conn


def _pick_ble(seconds=6.0):
    try:
        found = [d for d in ble.scan(seconds) if d["likely"]]
    except ble.LinkError as e:
        raise NotConnected(str(e)) from e
    if len(found) == 1:
        d = found[0]
        print(f"BLE adapter: {d['name'] or '(no name)'} {d['address']} ({d['why']})")
        return "ble://" + d["address"]
    if not found:
        raise NotConnected("No BLE OBD adapter seen. Plugged in and lit? No phone connected to it? "
                           "Run --adapters to list everything in range.")
    raise NotConnected("Several BLE adapters in range; pick one with --port ble:<address>:\n" +
                       "\n".join(f"  {d['address']}  {d['name']}" for d in found))


def connect(port="auto", timeout=0.5, fast=False, check_voltage=True, protocol=None):
    """Open an ELM327 on any transport. Returns an Adapter whose .conn is a python-OBD connection,
    which may stop short of the car (check conn.status()). Raises NotConnected if no adapter answers."""
    url = normalize(port)
    if url.lower() == "auto":
        conn = obd.OBD(None, fast=fast, timeout=timeout, check_voltage=check_voltage, protocol=protocol)
        if conn.status() != obd.OBDStatus.NOT_CONNECTED:
            ad = Adapter(conn.port_name(), conn)
            identify(ad)
            return ad
        if ble.BleakClient is None:
            raise NotConnected("No serial adapter answered (and BLE scanning needs the 'bleak' package).")
        print("No serial adapter answered; scanning for BLE adapters...")
        url = _pick_ble()
    elif url == "ble://":
        url = _pick_ble()
    elif kind(url) in ("USB/serial", "Bluetooth Classic") and "://" not in url:
        if not os.path.exists(url):
            raise NotConnected(f"{url} doesn't exist. Run --adapters to see what's plugged in.")
        if not os.access(url, os.R_OK | os.W_OK):
            raise NotConnected(f"No permission to open {url}. " + PERMISSION_HELP)
    conn = _open(url, timeout, fast, check_voltage, protocol)
    if conn.status() == obd.OBDStatus.NOT_CONNECTED:
        raise NotConnected(ble.last_error or help_for(url))
    ad = Adapter(url, conn, ble.last_link if kind(url) == "BLE" else None)
    identify(ad)
    return ad


def help_for(url):
    k = kind(url)
    if k == "BLE":
        return "BLE adapter didn't answer as an ELM327. Unplug it from the car for 10 s and retry."
    if k == "Wi-Fi":
        return "Nothing at that address. Is the laptop joined to the adapter's Wi-Fi network?"
    if k == "Bluetooth Classic":
        return "rfcomm port didn't answer. Paired and bound? (bluetoothctl pair, then rfcomm bind)"
    return "Port opened but no ELM327 answered. Unplug the adapter from the laptop and back in, then retry."


# --- identifying the chip -------------------------------------------------------

# Read-only queries: none of these change the adapter's settings or talk to the car.
IDENT = [("chip", "ATI"), ("device", "AT@1"), ("stn", "STI"), ("volts", "ATRV"), ("protocol", "ATDP")]


def at(conn, cmd):
    """Send one AT/ST command, return its reply lines joined. Bypasses python-OBD's OBD parsing.
    Only for queries that change nothing: python-OBD tracks the current header and protocol itself,
    so an ATSH/ATSP sent here would send its next query to the wrong place. Use an OBDCommand with
    a header instead, as honda.py does."""
    msgs = conn.interface.send_and_parse(cmd.encode()) if conn.interface else None
    text = " / ".join(m.raw() for m in msgs or [] if m.raw())
    return text.strip()


def identify(adapter):
    """Fill adapter.ident with what the adapter says it is, plus notes on what that means."""
    ident = {}
    for key, cmd in IDENT:
        try:
            ident[key] = at(adapter.conn, cmd)
        except Exception as e:  # a dead link during identify is reported by the caller's status check
            ident[key] = f"(error: {e})"
    ident["type"], notes = classify(ident, adapter.kind)
    if ident.get("device") in ("", "?"):
        notes.append("no AT@1 description (clones often leave it blank)")
    ident["notes"] = notes
    adapter.ident = ident
    return ident


def classify(ident, link_kind):
    """Which adapter type this is, measured against REFERENCE. Returns (type, notes)."""
    chip = ident.get("chip", "")
    ref = REFERENCE["name"]
    if ident.get("stn") and "?" not in ident["stn"]:
        return "STN", [f"STN chip ({ident['stn']}): an OBDLink/vLinker-class adapter, better than the "
                       f"{ref} this repo assumes. Everything here works on it"]
    if "v1.5" in chip:
        if link_kind == "BLE":
            return ref, [f"the {ref} this repo is built around"]
        return ref, [f"firmware matches the {ref}, reached over {link_kind}"
                     + (" (its Classic side)" if link_kind == "Bluetooth Classic" else "")]
    if "v2.1" in chip:
        return "v2.1 clone", [f"v2.1 minis are usually a cut-down clone, not the {ref} this repo assumes. "
                              "Standard OBD2 should work; expect header changes and Honda reads to fail"]
    if chip.startswith("ELM327"):
        return "ELM327", [f"not the {ref} this repo assumes; standard OBD2 should work"]
    return "unknown", ["ATI didn't name an ELM327; is this an OBD adapter?"]


def show(adapter, verbose=False):
    i = adapter.ident
    print(f"Adapter: {i.get('chip') or '?'} over {adapter.kind} ({adapter.url}), type: {i.get('type', '?')}")
    if verbose:
        for key, cmd in IDENT:
            print(f"  {cmd:5} {i.get(key) or '(no answer)'}")
        if adapter.link is not None:
            for line in adapter.link.info():
                print(f"  {line}")
    for n in i.get("notes", []):
        print(f"  note: {n}")


# --- finding adapters -----------------------------------------------------------

def find_adapters(ble_seconds=6.0):
    """List what could be an adapter on every transport, without opening anything.
    Returns [(port to pass, kind, description)]."""
    out = []
    seen = set()
    for p in list_ports.comports():
        if p.vid is None and "rfcomm" not in p.device and "USB" not in p.device.upper():
            continue  # built-in UARTs (ttyS*) are never the adapter
        vidpid = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else ""
        bridge = USB_BRIDGES.get(vidpid, "")
        desc = " ".join(x for x in (p.description if p.description != "n/a" else "", bridge, vidpid) if x)
        out.append((p.device, kind(p.device), desc))
        seen.add(p.device)
    for dev in sorted(glob.glob("/dev/rfcomm*")):
        if dev not in seen:
            out.append((dev, "Bluetooth Classic", "bound rfcomm port"))
    if ble.BleakClient is None:
        out.append(("", "BLE", "not scanned: install 'bleak' (pip install -r yf/requirements.txt)"))
        return out
    try:
        for d in ble.scan(ble_seconds):
            if d["likely"]:
                out.append((f"ble:{d['address']}", "BLE", f"{d['name'] or '(no name)'}  rssi {d['rssi']}  ({d['why']})"))
    except ble.LinkError as e:
        out.append(("", "BLE", f"not scanned: {e}"))
    return out

