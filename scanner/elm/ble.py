"""Bluetooth Low Energy transport for ELM327 adapters, built for the ELM327 v1.5 BLE mini
(adapter.REFERENCE) and general enough for other BLE clones.

BLE adapters (the "Bluetooth 4.0/5.x, works with iPhone" minis) don't show up as a serial port.
They carry the ELM327's serial stream over two GATT characteristics: one the adapter *notifies*
on (adapter -> laptop) and one the laptop *writes* to (laptop -> adapter). Which ones differs by
clone, so this module knows the common layouts and, failing those, picks any service that has a
notify + write pair, probing with a harmless `ATI` when more than one could be it.

BleLink is a blocking byte pipe (read/write/close) over that pair, with bleak running on a
background event loop. protocol_ble.py wraps it as a pyserial port for the session (session.py).

Linux: needs BlueZ running (`systemctl start bluetooth`). No pairing, no rfcomm. Windows and macOS
use the OS Bluetooth stack through bleak. Most BLE adapters take one connection at a time, so close
any phone app that is connected to it.
"""

import asyncio
import re
import sys
import threading
import time

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:  # BLE is optional; USB / rfcomm / Wi-Fi work without bleak
    BleakClient = BleakScanner = None
    BleakError = Exception


class LinkError(Exception):
    """The BLE link could not be opened, or dropped."""


def _u(short):
    """16-bit SIG-style UUID -> full 128-bit string, the form bleak reports."""
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


# (label, service, notify characteristic, write characteristic). Reported layouts of BLE
# ELM327-class adapters, gathered from open-source OBD apps; none measured by this repo yet.
PROFILES = [
    ("FFF0 (ELM327 v1.5 BLE mini, expected)", _u("fff0"), _u("fff1"), _u("fff2")),
    ("FFE0 (HM-10 style module)", _u("ffe0"), _u("ffe1"), _u("ffe1")),
    ("18F0", _u("18f0"), _u("2af0"), _u("2af1")),
    ("Vgate/vLinker", "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
     "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"),
    ("Microchip transparent UART", "49535343-fe7d-4ae5-8fa9-9fafd205e455",
     "49535343-1e4d-4bd9-ba61-23c647249616", "49535343-8841-43f4-a8d4-ecbe34729bb3"),
    ("Nordic UART", "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
     "6e400003-b5a3-f393-e0a9-e50e24dcca9e", "6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
]
KNOWN_SERVICES = {p[1] for p in PROFILES}

# Standard services that are never the data pipe (GAP, GATT, device info, battery, HID, DFU...).
SKIP_SERVICES = {_u(s) for s in ("1800", "1801", "180a", "180f", "1805", "1812", "fe59")}

# Advertised names that suggest an OBD adapter. Many clones advertise just "OBDII" or "OBDBLE".
LIKELY_NAME = re.compile(r"obd|elm|v-?link|vgate|veepeak|viecar|konnwei|icar|carista|bafx|lelink|"
                         r"kiwi|viabecs|ymobd|scan", re.I)

ADDRESS = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$|^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)


def hint(err=""):
    """What to try for a BLE failure, for this OS."""
    e = str(err)
    if "BREDR" in e or "ProfileUnavailable" in e or "br-connection" in e:
        return ("BlueZ tried classic Bluetooth on an LE-only adapter. Set 'ControllerMode = le' in "
                "/etc/bluetooth/main.conf and restart bluetooth (setup/setup.sh --le-only does this)")
    if sys.platform == "win32":
        return "Is Bluetooth on (Settings > Bluetooth & devices)? Close any phone app connected to the adapter"
    if sys.platform == "darwin":
        return "Is Bluetooth on, and has the terminal been allowed to use it (System Settings > Privacy)?"
    if "org.bluez" in e or "ServiceUnknown" in e:
        return ("BlueZ isn't running or there's no radio. Linux: sudo systemctl start bluetooth. "
                "WSL: attach the radio with usbipd first (setup/wsl-attach-bluetooth.ps1)")
    return "Is Bluetooth on (sudo systemctl start bluetooth)? Close any phone app connected to the adapter"


def require_bleak():
    if BleakClient is None:
        raise LinkError("BLE needs the 'bleak' package: uv sync")


class Channel:
    """One notify + write characteristic pair."""

    def __init__(self, label, service, notify, write):
        self.label, self.service, self.notify, self.write = label, service, notify, write

    @property
    def write_response(self):
        """Write without response when allowed (what phone apps do, lower latency)."""
        return "write-without-response" not in self.write.properties

    def __str__(self):
        mode = "with response" if self.write_response else "without response"
        return (f"{self.label}: service {self.service.uuid}, notify {self.notify.uuid}, "
                f"write {self.write.uuid} ({mode})")


def _notifies(ch):
    return "notify" in ch.properties or "indicate" in ch.properties


def _writes(ch):
    return "write" in ch.properties or "write-without-response" in ch.properties


def channels(services, notify=None, write=None):
    """Candidate channels in the order to try them: an explicit notify/write UUID pair, then known
    layouts, then any other service with a notify + write pair."""
    chars = {c.uuid.lower(): (s, c) for s in services for c in s.characteristics}
    if notify or write:
        n, w = chars.get((notify or "").lower()), chars.get((write or notify or "").lower())
        if not n or not w:
            raise LinkError(f"characteristic not found: notify={notify} write={write}")
        return [Channel("given", n[0], n[1], w[1])]
    out = []
    for label, svc, n, w in PROFILES:
        if n in chars and w in chars and chars[n][0].uuid.lower() == svc:
            out.append(Channel(label, chars[n][0], chars[n][1], chars[w][1]))
    known = {c.service.uuid.lower() for c in out}
    for s in services:
        if s.uuid.lower() in known or s.uuid.lower() in SKIP_SERVICES:
            continue
        ns = [c for c in s.characteristics if _notifies(c)]
        ws = [c for c in s.characteristics if _writes(c)]
        both = [c for c in ns if c in ws]
        if both:
            out.append(Channel("generic", s, both[0], both[0]))
        elif ns and ws:
            out.append(Channel("generic", s, ns[0], ws[0]))
    return out


def describe_services(services):
    lines = []
    for s in services:
        lines.append(f"  service {s.uuid}")
        for c in s.characteristics:
            lines.append(f"    {c.uuid}  {','.join(c.properties)}")
    return lines


# --- scanning -------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def scan(seconds=6.0):
    """Every BLE device in range, likely OBD adapters first. Each: dict(address, name, rssi, likely, why)."""
    require_bleak()

    async def go():
        return await BleakScanner.discover(timeout=seconds, return_adv=True)

    try:
        found = _run(go())
    except BleakError as e:
        raise LinkError(f"BLE scan failed: {e}. {hint(e)}") from e
    out = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        svcs = {u.lower() for u in adv.service_uuids or []}
        why = []
        if svcs & KNOWN_SERVICES:
            why.append("advertises an OBD adapter service")
        if name and LIKELY_NAME.search(name):
            why.append("name")
        out.append({"address": addr, "name": name, "rssi": adv.rssi, "likely": bool(why), "why": ", ".join(why)})
    out.sort(key=lambda d: (not d["likely"], -(d["rssi"] or -999)))
    return out


# --- the link -------------------------------------------------------------------

class BleLink:
    """Blocking byte pipe to a BLE ELM327. Notifications land in a buffer; read() drains it."""

    def __init__(self, target, notify=None, write=None, connect_timeout=20.0, scan_timeout=10.0):
        require_bleak()
        self.target = target
        self.notify_uuid, self.write_uuid = notify, write
        self.connect_timeout, self.scan_timeout = connect_timeout, scan_timeout
        self.channel = None
        self.client = None
        self.device = None
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._lost = False
        self._loop = None
        self._thread = None

    # -- lifecycle

    def open(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ble-elm", daemon=True)
        self._thread.start()
        try:
            self._call(self._open(), self.connect_timeout + self.scan_timeout + 10)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._loop is None:
            return
        if self.client is not None:
            try:
                self._call(self._close(), 5)
            except Exception:
                pass
            self.client = None
        try:
            self._call(self._cancel_tasks(), 3)  # bleak leaves monitor tasks running; end them quietly
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(2)
        if not self._loop.is_running():
            self._loop.close()
        self._loop = None

    def _call(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _find(self):
        if ADDRESS.match(self.target):
            dev = await BleakScanner.find_device_by_address(self.target, timeout=self.scan_timeout)
        else:
            dev = await BleakScanner.find_device_by_name(self.target, timeout=self.scan_timeout)
        if dev is None:
            raise LinkError(f"BLE adapter {self.target} not seen. Is it plugged into the car (its light on)? "
                            "Is a phone app still connected to it? BLE adapters take one connection at a time.")
        return dev

    async def _open(self):
        try:
            self.device = await self._find()
            self.client = BleakClient(self.device, disconnected_callback=self._on_disconnect,
                                      timeout=self.connect_timeout)
            await self.client.connect()
            cands = channels(self.client.services, self.notify_uuid, self.write_uuid)
            if not cands:
                raise LinkError("no notify + write characteristic pair on this device; is it an OBD adapter?\n"
                                + "\n".join(describe_services(self.client.services)))
            for ch in cands:
                await self.client.start_notify(ch.notify, self._on_data)
                if len(cands) == 1 or await self._probe(ch):
                    self.channel = ch
                    break
                await self.client.stop_notify(ch.notify)
            if self.channel is None:
                raise LinkError("no characteristic pair answered ATI:\n" + "\n".join(str(c) for c in cands))
            with self._cv:
                self._buf.clear()
        except BleakError as e:
            raise LinkError(f"BLE: {e}. {hint(e)}") from e

    @staticmethod
    async def _cancel_tasks():
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _close(self):
        if self.channel is not None and self.client.is_connected:
            try:
                await self.client.stop_notify(self.channel.notify)
            except Exception:
                pass
        await self.client.disconnect()

    async def _probe(self, ch, seconds=2.0):
        """Some clones expose two pipes and wire only one to the ELM chip. ATI is read-only."""
        with self._cv:
            self._buf.clear()
        await self._write(ch, b"ATI\r")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            with self._cv:
                if b">" in self._buf:
                    return True
            await asyncio.sleep(0.05)
        with self._cv:
            return bool(self._buf)

    # -- callbacks (event-loop thread)

    def _on_data(self, _char, data):
        with self._cv:
            self._buf.extend(data)
            self._cv.notify_all()

    def _on_disconnect(self, _client):
        with self._cv:
            self._lost = True
            self._cv.notify_all()

    # -- byte pipe (caller's thread)

    async def _write(self, ch, data):
        size = max(20, ch.write.max_write_without_response_size)
        for i in range(0, len(data), size):
            await self.client.write_gatt_char(ch.write, data[i:i + size], response=ch.write_response)

    def write(self, data):
        if self._lost or self.client is None:
            raise LinkError("BLE link dropped")
        self._call(self._write(self.channel, bytes(data)), 10)
        return len(data)

    def read(self, size, timeout):
        end = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while not self._buf:
                if self._lost:
                    raise LinkError("BLE link dropped")
                left = None if end is None else end - time.monotonic()
                if left is not None and left <= 0:
                    return b""
                self._cv.wait(left)
            out = bytes(self._buf[:size])
            del self._buf[:size]
            return out

    @property
    def in_waiting(self):
        with self._cv:
            return len(self._buf)

    def reset_input(self):
        with self._cv:
            self._buf.clear()

    def info(self):
        lines = [f"BLE device {self.device.address} ({self.device.name or 'no name'})",
                 f"  channel {self.channel}"]
        try:
            lines.append(f"  MTU {self.client.mtu_size}")
        except Exception:
            pass
        lines += describe_services(self.client.services)
        return lines


# The most recently opened link, so adapter.py can describe it (the pyserial wrapper hides it).
last_link = None
last_error = None
