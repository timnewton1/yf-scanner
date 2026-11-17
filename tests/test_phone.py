"""scanner/phone.py, the phone app's bridge, with fakes for the Kotlin side: a Link that is the
simulated adapter (what BleLink.kt is on the car) and the hooks the app passes. Connect, health,
a query and a refused write, a drive log with a marker and a dropped link, the summary."""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["YF_ADAPTER_FILE"] = os.path.join(tempfile.mkdtemp(), ".adapter")  # never the repo's own .adapter

import serial  # noqa: E402

from scanner import phone  # noqa: E402


class FakeLink:
    """BleLink.kt's interface over the simulator: write(bytes), read(ms) -> bytes, resetInput, close, info."""

    def __init__(self):
        self.port = serial.serial_for_url("sim://pilot?latency=0")

    def write(self, data):
        if self.port.broken:
            raise RuntimeError("BLE link dropped")
        self.port.write(data)

    def read(self, ms):
        self.port.timeout = ms / 1000
        return self.port.read(self.port.in_waiting or 1)

    def resetInput(self):
        self.port.reset_input_buffer()

    def close(self):
        self.port.close()

    def info(self):
        return ["BLE device AA:BB:CC:DD:EE:01 (OBDII)", "channel FFF0"]


class Hooks:
    def __init__(self):
        self.links, self.sections, self.rows, self.statuses, self.seen = [], [], [], [], []
        self.marks = []
        self.stop = threading.Event()
        self.on_row = None

    def openLink(self, address):
        self.links.append(FakeLink())
        return self.links[-1]

    def traffic(self, d, t):
        self.seen.append(f"{d} {t}")

    def section(self, js):
        self.sections.append(json.loads(js))

    def row(self, js):
        self.rows.append(json.loads(js))
        if self.on_row:
            self.on_row(self.rows[-1])

    def status(self, text):
        self.statuses.append(text)

    def stopRequested(self):
        return self.stop.is_set()

    def takeMarker(self):
        out = ";".join(self.marks)
        self.marks.clear()
        return out


class TestPhone(unittest.TestCase):
    def setUp(self):
        self.h = Hooks()
        r = json.loads(phone.connect("AA:BB:CC:DD:EE:01", self.h))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["car"])
        self.connected = r

    def tearDown(self):
        phone.disconnect()

    def test_hello(self):
        h = json.loads(phone.hello())
        self.assertEqual(set(h["sets"]), {"drive", "noise", "all"})
        self.assertIn("0000fff0-0000-1000-8000-00805f9b34fb", [p[1] for p in h["profiles"]])

    def test_connect_describes_adapter_and_car(self):
        text = json.dumps(self.connected)
        self.assertIn("ELM327 v1.5", text)
        self.assertIn("transmission (TCM)", text)
        self.assertIn("channel FFF0", text)  # the link's own info() reaches the Adapter section
        self.assertTrue(any(t.startswith(">") for t in self.h.seen))

    def test_health_streams_sections(self):
        out = json.loads(phone.run("health"))
        self.assertEqual([s["title"] for s in out], [s["title"] for s in self.h.sections])
        text = json.dumps(out)
        for want in ("P3400", "INCOMPLETE  EVAP system", "Highest: cylinder 3", "Honda reads"):
            self.assertIn(want, text)

    def test_query_and_refused_write(self):
        ok = json.loads(phone.run("query", "222201", "1D"))[0]
        self.assertIn("bytes:", ok["lines"][-1]["text"])
        refused = json.loads(phone.run("query", "04", "engine"))[0]
        self.assertEqual(refused["title"], "Refused")

    def test_log_with_marker_and_dropped_link_then_summary(self):
        with tempfile.TemporaryDirectory() as d:
            prep = json.loads(phone.log_prepare("noise", True, "t", d))
            self.assertIn("ATF F (candidate)", [label for _, label in prep["tiles"]])

            def each(row):
                n = len(self.h.rows)
                if n == 2:
                    self.h.marks.append("whine")
                if n == 4:
                    self.h.links[-1].port.broken = True  # the BLE link drops; the logger reconnects
                if n == 8:
                    self.h.stop.set()
            self.h.on_row = each
            self.addCleanup(setattr, phone.logger.Logger, "retry_delay", phone.logger.Logger.retry_delay)
            phone.logger.Logger.retry_delay = 0.05
            done = json.loads(phone.log_run())
            self.assertEqual(done["why"], "stopped")
            self.assertTrue(done["connected"])
            self.assertIn("Saved yf_", done["status"])
            self.assertIn("Reconnected.", self.h.statuses)
            self.assertGreaterEqual(len(self.h.links), 2)
            self.assertIn("whine", [r["marker"] for r in self.h.rows])
            text = phone.summary(prep["path"])
            self.assertIn("'whine' at", text)
            self.assertIn("Link: 1 drop(s)", text)


class TestOnAndroid(unittest.TestCase):
    def test_imports_and_demo_where_sys_platform_is_android(self):
        """On the phone sys.platform is 'android', which some of pyserial refuses to import on."""
        import subprocess
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        code = ("import sys, json; sys.platform = 'android'; sys.path.insert(0, '.')\n"
                "from scanner import phone\n"
                "class H:\n"
                "    def traffic(self, d, t): pass\n"
                "    def section(self, js): pass\n"
                "r = json.loads(phone.connect('DEMO', H())); assert r['car'], r\n"
                "assert 'P3400' in phone.run('codes')\n"
                "print('ok')")
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "YF_ADAPTER_FILE": os.environ["YF_ADAPTER_FILE"]})
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)


if __name__ == "__main__":
    unittest.main()
