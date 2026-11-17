"""Transport-agnostic adapter handling: name the port, open it, start an ELM327 session, say what
the adapter is.

Everything here is the same for USB, Bluetooth Classic, Wi-Fi, BLE and the simulator. The only
per-transport choice is whether to probe for a baud rate (only a real UART has one).
"""

import glob
import os
import sys
import time

import serial
from serial.tools import list_ports

from . import ble
from .session import Elm, ElmError

# The adapter type this repo is built around. Confirmed on our unit (a ViaBecs OBD-HT) on
# 2026-11-16: ATI "ELM327 v1.5", AT@1 "OBDII to RS232 Adapter", GATT FFF0/FFF1/FFF2. See docs/adapters.md.
REFERENCE = {
    "name": "ELM327 v1.5 BLE mini",
    "firmware": "reports 'ELM327 v1.5' to ATI: clone firmware, ELM Electronics never released a v1.5",
    "chip": "PIC18F25K80 microcontroller plus a separate Bluetooth module (two-board build; per sellers)",
    "link": "Bluetooth LE (what iPhone apps use)",
    "gatt": "service FFF0: notify FFF1, write FFF2 (confirmed on our unit)",
    "quirks": "answers OK to AT commands it doesn't implement; returns only the first reply to a "
              "broadcast request when two modules answer; drops a BLE frame now and then",
    "examples": ["ViaBecs OBD-HT (ours)"],
}

# USB-serial bridges ELM327 clones are built on, by USB vendor:product.
USB_BRIDGES = {
    "1a86:7523": "CH340", "1a86:5523": "CH341", "0403:6001": "FTDI FT232", "0403:6015": "FTDI FT231X",
    "10c4:ea60": "CP210x", "067b:2303": "PL2303",
}

# Baud rates ELM327 clones ship with, most common first.
BAUDS = (38400, 9600, 115200, 57600, 230400, 500000)

PERMISSION_HELP = ("Add yourself to the group that owns it (ls -l shows it: usually 'dialout', 'uucp' on some "
                   "distros): sudo usermod -aG <group> $USER, then log out and in.")


class NotConnected(Exception):
    """No ELM327 answered on the given port."""


# --- the saved adapter ----------------------------------------------------------
# Once an adapter answers, its port goes in <repo>/.adapter (gitignored), so `--port auto` (the
# default) goes straight to it next time and no address has to be written anywhere in the repo.
# YF_ADAPTER_FILE points it elsewhere (the tests do).

SAVED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".adapter")


def saved_path():
    return os.path.abspath(os.environ.get("YF_ADAPTER_FILE") or SAVED_FILE)


def saved():
    """The port of the last adapter that answered, as you'd pass it to --port, or None."""
    try:
        with open(saved_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return None


def remember(url):
    """Save a real adapter's port (never the simulator). Returns the saved port, or None."""
    if kind(url) == "simulator":
        return None
    port = "ble:" + url[len("ble://"):] if url.lower().startswith("ble://") else url
    if port == saved():
        return port
    try:
        with open(saved_path(), "w", encoding="utf-8") as f:
            f.write("# The adapter yf last connected to (written by elm/adapter.py; not in git).\n"
                    "# --port auto, the default, uses it. Delete this file, or run --forget-adapter, to scan again.\n"
                    f"{port}\n")
    except OSError:
        return None
    return port


def forget():
    """Delete the saved adapter. Returns whether there was one."""
    try:
        os.remove(saved_path())
        return True
    except OSError:
        return False


class Adapter:
    """An open adapter: .elm is the session, .car is whether the car answered at setup."""

    def __init__(self, url, elm, link=None):
        self.url, self.elm, self.link = url, elm, link
        self.kind = kind(url)
        self.ident = {}
        self.car = False

    def close(self):
        self.elm.close()


# --- naming ports ---------------------------------------------------------------

def normalize(port):
    """User-facing port name -> what serial_for_url opens.
    'ble' (scan) -> 'ble://', 'ble:AA:BB:..' -> 'ble://AA:BB:..', 'DEMO' -> 'sim://pilot';
    everything else passes through: a device path, a COM port, or any pyserial URL
    (socket://host:port for Wi-Fi adapters)."""
    p = port.strip()
    low = p.lower()
    if low == "demo":
        return "sim://pilot"
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
    if low.startswith("sim://"):
        return "simulator"
    if "rfcomm" in low:
        return "Bluetooth Classic"
    return "USB/serial"


# --- opening --------------------------------------------------------------------

def _answers(port, wait=0.4):
    """Does an ELM327 answer on this port at its current settings? Sends a harmless ATI."""
    try:
        port.reset_input_buffer()
        port.write(b"\x7f\x7f\rATI\r")
        end, buf = time.monotonic() + wait, b""
        while time.monotonic() < end:
            buf += port.read(port.in_waiting or 1)
            if b">" in buf:
                return True
    except (serial.SerialException, OSError):
        return False
    return False


def open_port(url, baud=None):
    """Open the transport. UARTs get their baud rate probed unless given."""
    k = kind(url)
    if k not in ("USB/serial", "Bluetooth Classic"):
        return serial.serial_for_url(url, timeout=0.05)
    if "://" not in url:
        if not os.path.exists(url) and not url.upper().startswith("COM"):
            raise NotConnected(f"{url} doesn't exist. Run --adapters to see what's plugged in.")
        if os.path.exists(url) and not os.access(url, os.R_OK | os.W_OK):
            raise NotConnected(f"No permission to open {url}. " + PERMISSION_HELP)
    port = serial.serial_for_url(url, baudrate=baud or BAUDS[0], timeout=0.05)
    if baud:
        return port
    for b in BAUDS:
        port.baudrate = b
        if _answers(port):
            return port
    port.close()
    raise NotConnected(f"{url} opened, but no ELM327 answered at any of {', '.join(map(str, BAUDS))} baud. "
                       "Unplug the adapter from the laptop and back in, then retry.")


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
    raise NotConnected("Several BLE adapters in range; pick one with --port ble:<address> "
                       "(it's remembered after that):\n" +
                       "\n".join(f"  {d['address']}  {d['name']}" for d in found))


def _auto():
    """First serial adapter that answers, else the one BLE adapter in range."""
    for port, k, _ in find_adapters(ble_seconds=0):
        if port and k in ("USB/serial", "Bluetooth Classic"):
            try:
                return port, open_port(port)
            except (NotConnected, serial.SerialException, OSError):
                continue
    if ble.BleakClient is None:
        raise NotConnected("No serial adapter answered (and BLE scanning needs the 'bleak' package).")
    print("No serial adapter answered; scanning for BLE adapters...")
    url = _pick_ble()
    return url, open_port(url)


def connect(port="auto", timeout=1.0, protocol=None, baud=None, log=None, car=True):
    """Open an adapter on any transport and start a session. car=True also finds the protocol and
    checks the car answers (Adapter.car says whether it did). Raises NotConnected if no adapter answers.

    port='auto' tries the saved adapter (.adapter) first, then looks for one. Any real adapter that
    answers is saved for next time."""
    if normalize(port).lower() == "auto" and saved():
        prev = saved()
        try:
            return _connect(prev, timeout, protocol, baud, log, car)
        except NotConnected as e:
            print(f"Saved adapter {prev} didn't answer ({str(e)[:100]}); looking for others...")
    ad = _connect(port, timeout, protocol, baud, log, car)
    remember(ad.url)
    return ad


def _connect(port, timeout, protocol, baud, log, car):
    url = normalize(port)
    ble.last_link = ble.last_error = None
    try:
        if url.lower() == "auto":
            url, sp = _auto()
        else:
            if url == "ble://":
                url = _pick_ble()
            sp = open_port(url, baud)
    except serial.SerialException as e:
        raise NotConnected(ble.last_error or f"{e}. {help_for(url)}") from e
    elm = Elm(sp, timeout=timeout, log=log)
    try:
        elm.reset()
    except ElmError as e:
        elm.close()
        if kind(url) != "BLE":
            raise NotConnected(f"{e}. {help_for(url)}") from e
        # BLE links on the reference adapter sometimes drop right after connecting: one more go
        time.sleep(2)
        try:
            sp = open_port(url, baud)
            elm = Elm(sp, timeout=timeout, log=log)
            elm.reset()
        except (ElmError, serial.SerialException) as e2:
            elm.close()
            raise NotConnected(f"{ble.last_error or e2}. {help_for(url)}") from e2
    ad = Adapter(url, elm, ble.last_link if kind(url) == "BLE" else None)
    if car:
        ad.car = elm.setup(protocol)
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


def identify(adapter):
    """Fill adapter.ident with what the adapter says it is, plus notes on what that means."""
    ident = {}
    for key, cmd in IDENT:
        try:
            ident[key] = adapter.elm.at(cmd)
        except ElmError as e:
            ident[key] = f"(error: {e})"
    ident["type"], notes = classify(ident, adapter.kind)
    if ident.get("device") in ("", "?"):
        notes.append("no AT@1 description (clones often leave it blank)")
    ident["notes"] = notes
    adapter.ident = ident
    return ident


def classify(ident, link_kind):
    """Which adapter type this is, measured against REFERENCE. Returns (type, notes).

    A real STN chip answers STI with its name ("STN2120 v5.6.19"), a real ELM327 with "?". Some
    clones answer "OK" to anything, STI included: that is not an STN."""
    chip = ident.get("chip", "")
    stn = ident.get("stn", "").strip()
    ref = REFERENCE["name"]
    notes = []
    if stn.upper().startswith("STN"):
        return "STN", [f"STN chip ({stn}): an OBDLink/vLinker-class adapter, better than the "
                       f"{ref} this repo assumes. Everything here works on it"]
    if stn.upper() == "OK":
        notes.append("answers OK to commands it doesn't implement (STI said OK), so an OK from this "
                     "adapter doesn't prove a setting took")
    if link_kind == "simulator":
        return ref, notes + [f"simulated {ref}"]
    if "v1.5" in chip:
        if link_kind == "BLE":
            return ref, notes + [f"the {ref} this repo is built around"]
        return ref, notes + [f"firmware matches the {ref}, reached over {link_kind}"
                             + (" (its Classic side)" if link_kind == "Bluetooth Classic" else "")]
    if "v2.1" in chip:
        return "v2.1 clone", notes + [f"v2.1 minis are usually a cut-down clone, not the {ref} this repo "
                                      "assumes. Standard OBD2 should work; Honda reads may fail"]
    if chip.upper().startswith("ELM327"):
        return "ELM327", notes + [f"not the {ref} this repo assumes; standard OBD2 should work"]
    return "unknown", notes + ["ATI didn't name an ELM327; is this an OBD adapter?"]


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
    Returns [(port to pass, kind, description)]. ble_seconds=0 skips the BLE scan."""
    out = []
    seen = set()
    for p in list_ports.comports():
        builtin = p.vid is None and "rfcomm" not in p.device and "USB" not in p.device.upper()
        if builtin and not (sys.platform == "win32" and "bluetooth" in (p.description or "").lower()):
            continue  # built-in UARTs (ttyS*) are never the adapter; Windows lists BT Classic SPP as COM ports
        vidpid = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else ""
        bridge = USB_BRIDGES.get(vidpid, "")
        desc = " ".join(x for x in (p.description if p.description != "n/a" else "", bridge, vidpid) if x)
        out.append((p.device, kind(p.device), desc))
        seen.add(p.device)
    for dev in sorted(glob.glob("/dev/rfcomm*")):
        if dev not in seen:
            out.append((dev, "Bluetooth Classic", "bound rfcomm port"))
    if not ble_seconds:
        return out
    if ble.BleakClient is None:
        out.append(("", "BLE", "not scanned: install 'bleak' (pip install -r requirements.txt)"))
        return out
    try:
        for d in ble.scan(ble_seconds):
            if d["likely"]:
                out.append((f"ble:{d['address']}", "BLE", f"{d['name'] or '(no name)'}  rssi {d['rssi']}  ({d['why']})"))
    except ble.LinkError as e:
        out.append(("", "BLE", f"not scanned: {e}"))
    return out
