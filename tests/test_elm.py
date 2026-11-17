"""The transports, end to end through the real session and car layer: a simulated BLE adapter
and a pty standing in for a USB one, both with the simulated Pilot behind them. No radio needed.

    .venv/bin/python -m unittest discover -s tests -v
"""

import asyncio
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["YF_ADAPTER_FILE"] = os.path.join(tempfile.mkdtemp(), ".adapter")  # never the repo's own .adapter

from scanner import elm  # noqa: E402
from scanner.car import Car  # noqa: E402
from scanner.elm import ble  # noqa: E402
from scanner.sim.elm import ElmEmulator  # noqa: E402
from scanner.sim.pilot import Pilot  # noqa: E402


def ElmSim():
    return ElmEmulator(Pilot())

U = ble._u


# --- fake bleak -----------------------------------------------------------------

class Char:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props
        self.max_write_without_response_size = 20


class Service:
    def __init__(self, uuid, chars):
        self.uuid, self.characteristics = uuid, chars


class Device:
    def __init__(self, address, name):
        self.address, self.name = address, name


class Adv:
    def __init__(self, name, uuids, rssi):
        self.local_name, self.service_uuids, self.rssi = name, uuids, rssi


class FakeAdapter:
    """A BLE ELM327: GATT layout plus which characteristic pair is wired to the ELM chip."""

    def __init__(self, services, wired_notify, wired_write, address="AA:BB:CC:DD:EE:01", name="OBDII"):
        self.services, self.wired = services, (wired_notify, wired_write)
        self.address, self.name = address, name
        self.sim = ElmSim()
        self.client = None


WORLD = []  # FakeAdapters "in range"


class FakeScanner:
    @staticmethod
    async def find_device_by_address(addr, timeout=10):
        for a in WORLD:
            if a.address.lower() == addr.lower():
                return Device(a.address, a.name)

    @staticmethod
    async def find_device_by_name(name, timeout=10):
        for a in WORLD:
            if a.name == name:
                return Device(a.address, a.name)

    @staticmethod
    async def discover(timeout=5, return_adv=False):
        return {a.address: (Device(a.address, a.name), Adv(a.name, [], -60)) for a in WORLD}


class FakeClient:
    def __init__(self, device, disconnected_callback=None, timeout=30):
        self.adapter = next(a for a in WORLD if a.address == device.address)
        self.adapter.client = self
        self.on_disconnect = disconnected_callback
        self.callbacks = {}
        self.is_connected = False
        self.mtu_size = 23

    @property
    def services(self):
        return self.adapter.services

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, char, cb):
        self.callbacks[char.uuid] = cb

    async def stop_notify(self, char):
        self.callbacks.pop(char.uuid, None)

    async def write_gatt_char(self, char, data, response=None):
        n, w = self.adapter.wired
        if char.uuid != w:
            return  # a pipe that goes nowhere
        out = self.adapter.sim.feed(bytes(data))
        cb = self.callbacks.get(n)
        loop = asyncio.get_running_loop()
        for i in range(0, len(out), 20):  # replies arrive as 20-byte notifications, like a real one
            if cb:
                loop.call_soon(cb, char, bytearray(out[i:i + 20]))

    def drop(self):
        self.is_connected = False
        self.on_disconnect(self)


def ref_adapter(**kw):
    """The reference type as expected: FFF0 service, FFF1 notify, FFF2 write, plus the usual GAP service."""
    n, w = Char(U("fff1"), ["notify"]), Char(U("fff2"), ["write", "write-without-response"])
    gap = Service(U("1800"), [Char(U("2a00"), ["read"])])
    return FakeAdapter([gap, Service(U("fff0"), [n, w])], n.uuid, w.uuid, **kw)


class BleTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = ble.BleakClient, ble.BleakScanner
        ble.BleakClient, ble.BleakScanner = FakeClient, FakeScanner
        WORLD.clear()

    def tearDown(self):
        ble.BleakClient, ble.BleakScanner = self._saved
        WORLD.clear()


# --- tests ----------------------------------------------------------------------

class TestPortNames(unittest.TestCase):
    def test_normalize_and_kind(self):
        self.assertEqual(elm.normalize("ble"), "ble://")
        self.assertEqual(elm.normalize("ble:AA:BB:CC:DD:EE:FF"), "ble://AA:BB:CC:DD:EE:FF")
        self.assertEqual(elm.normalize("/dev/ttyUSB0"), "/dev/ttyUSB0")
        self.assertEqual(elm.normalize("DEMO"), "sim://pilot")
        self.assertEqual(elm.kind("sim://pilot"), "simulator")
        self.assertEqual(elm.kind("ble://x"), "BLE")
        self.assertEqual(elm.kind("socket://192.168.0.10:35000"), "Wi-Fi")
        self.assertEqual(elm.kind("/dev/rfcomm0"), "Bluetooth Classic")
        self.assertEqual(elm.kind("/dev/ttyUSB0"), "USB/serial")


class TestSaved(unittest.TestCase):
    def setUp(self):
        elm.forget()

    def test_remember_forget(self):
        self.assertIsNone(elm.saved())
        self.assertIsNone(elm.remember("sim://pilot"), "the simulator is never saved")
        self.assertIsNone(elm.saved())
        self.assertEqual(elm.remember("/dev/ttyUSB0"), "/dev/ttyUSB0")
        self.assertEqual(elm.saved(), "/dev/ttyUSB0")
        self.assertTrue(elm.forget())
        self.assertFalse(elm.forget())

    def test_auto_uses_the_saved_port(self):
        with open(elm.adapter.saved_path(), "w", encoding="utf-8") as f:
            f.write("# comment\nsim://pilot?clean&latency=0\n")
        ad = elm.connect("auto")
        try:
            self.assertEqual(ad.url, "sim://pilot?clean&latency=0")
        finally:
            ad.close()

    def test_explicit_port_ignores_the_saved_one(self):
        elm.remember("/dev/does-not-exist")
        ad = elm.connect("sim://pilot?clean&latency=0")
        ad.close()
        self.assertEqual(elm.saved(), "/dev/does-not-exist")


class TestClassify(unittest.TestCase):
    def test_types(self):
        c = elm.adapter.classify
        self.assertEqual(c({"chip": "ELM327 v1.5", "stn": "?"}, "BLE")[0], elm.REFERENCE["name"])
        self.assertEqual(c({"chip": "ELM327 v1.5", "stn": "OK"}, "BLE")[0], elm.REFERENCE["name"])
        self.assertEqual(c({"chip": "ELM327 v2.1", "stn": "?"}, "BLE")[0], "v2.1 clone")
        self.assertEqual(c({"chip": "ELM327 v1.4b", "stn": "STN2120 v5.6.19"}, "USB/serial")[0], "STN")
        self.assertEqual(c({"chip": "?", "stn": "?"}, "BLE")[0], "unknown")


class TestChannels(unittest.TestCase):
    def test_known_profile_beats_generic(self):
        other = Service("12345678-0000-0000-0000-000000000000",
                        [Char("12345678-0000-0000-0000-000000000001", ["notify", "write"])])
        a = ref_adapter()
        chans = ble.channels([other] + a.services)
        self.assertEqual(chans[0].service.uuid, U("fff0"))
        self.assertEqual(chans[1].label, "generic")
        self.assertFalse(chans[0].write_response)

    def test_generic_skips_standard_services(self):
        weird = Service("abcd0000-0000-0000-0000-000000000000",
                        [Char("abcd0001-0000-0000-0000-000000000000", ["notify"]),
                         Char("abcd0002-0000-0000-0000-000000000000", ["write"])])
        gatt = Service(U("1801"), [Char(U("2a05"), ["indicate", "write"])])
        chans = ble.channels([gatt, weird])
        self.assertEqual(len(chans), 1)
        self.assertEqual(chans[0].notify.uuid, "abcd0001-0000-0000-0000-000000000000")
        self.assertTrue(chans[0].write_response)

    def test_explicit_uuids(self):
        a = ref_adapter()
        chans = ble.channels(a.services, notify=U("fff1"), write=U("fff2"))
        self.assertEqual(chans[0].label, "given")
        with self.assertRaises(ble.LinkError):
            ble.channels(a.services, notify=U("aaaa"))


class TestBle(BleTestCase):
    def test_car_over_ble(self):
        a = ref_adapter()
        WORLD.append(a)
        car = Car.open("ble:AA:BB:CC:DD:EE:01")
        try:
            self.assertTrue(car.connected)
            self.assertEqual(car.adapter.kind, "BLE")
            self.assertGreater(car.read(["RPM"])["RPM"], 500)
            self.assertEqual(car.adapter.ident["chip"], "ELM327 v1.5")
            self.assertEqual(car.adapter.ident["type"], elm.REFERENCE["name"])
            self.assertNotIn("\x7f\x7fATI", a.sim.sent, "baud probe should be skipped on BLE")
            self.assertIn("FFF0", str(car.adapter.link.channel))
        finally:
            car.close()

    def test_scan_picks_the_one_adapter(self):
        WORLD.append(ref_adapter())
        ad = elm.connect("ble")
        try:
            self.assertEqual(ad.url, "ble://AA:BB:CC:DD:EE:01")
        finally:
            ad.close()

    def test_probe_finds_the_wired_pipe(self):
        """A clone with two plausible pipes where only the second reaches the ELM chip."""
        n1, w1 = Char(U("fff1"), ["notify"]), Char(U("fff2"), ["write"])
        n2 = Char(U("ffe1"), ["notify", "write-without-response"])
        a = FakeAdapter([Service(U("fff0"), [n1, w1]), Service(U("ffe0"), [n2])], n2.uuid, n2.uuid)
        WORLD.append(a)
        ad = elm.connect("ble:AA:BB:CC:DD:EE:01")
        try:
            self.assertTrue(ad.car)
            self.assertIn("FFE0", str(ad.link.channel))
        finally:
            ad.close()

    def test_connected_adapter_is_saved_and_auto_uses_it(self):
        WORLD.append(ref_adapter())
        elm.forget()
        elm.connect("ble").close()
        self.assertEqual(elm.saved(), "ble:AA:BB:CC:DD:EE:01")
        with open(elm.adapter.saved_path(), encoding="utf-8") as f:
            self.assertIn("ble:AA:BB:CC:DD:EE:01", f.read())
        ad = elm.connect("auto")
        try:
            self.assertEqual(ad.url, "ble://AA:BB:CC:DD:EE:01")
        finally:
            ad.close()

    def test_auto_falls_back_when_the_saved_adapter_is_gone(self):
        elm.remember("ble://AA:BB:CC:DD:EE:99")
        WORLD.append(ref_adapter())
        ad = elm.connect("auto")
        try:
            self.assertEqual(ad.url, "ble://AA:BB:CC:DD:EE:01")
            self.assertEqual(elm.saved(), "ble:AA:BB:CC:DD:EE:01")
        finally:
            ad.close()

    def test_not_in_range(self):
        with self.assertRaises(elm.adapter.NotConnected) as cm:
            elm.connect("ble:AA:BB:CC:DD:EE:99")
        self.assertIn("not seen", str(cm.exception))

    def test_dropped_link_raises_link_lost(self):
        a = ref_adapter()
        WORLD.append(a)
        car = Car.open("ble:AA:BB:CC:DD:EE:01")
        a.client.drop()
        with self.assertRaises(elm.LinkLost):
            car.read(["SPEED"])
        car.close()


class TestSerial(unittest.TestCase):
    """Same layers over a pty: what a USB adapter looks like to the laptop (baud rate probed)."""

    def test_car_over_pty(self):
        master, slave = os.openpty()
        sim = ElmSim()
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    data = os.read(master, 1024)
                except OSError:
                    return
                out = sim.feed(data)
                if out:
                    os.write(master, out)

        threading.Thread(target=serve, daemon=True).start()
        car = Car.open(os.ttyname(slave))
        try:
            self.assertTrue(car.connected)
            self.assertEqual(car.adapter.kind, "USB/serial")
            self.assertEqual(car.read(["SPEED"])["SPEED"], 0.0)
            self.assertEqual(car.adapter.ident["type"], elm.REFERENCE["name"])  # same firmware, different link
            self.assertEqual(set(car.modules), {0x11, 0x1D})
        finally:
            car.close()
            stop.set()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
