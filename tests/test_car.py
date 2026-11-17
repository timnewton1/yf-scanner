"""The car layer and the CLI against the simulated adapter + Pilot (sim://), including the
reference adapter's quirks: one reply per broadcast, OK to anything, dropped BLE frames."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["YF_ADAPTER_FILE"] = os.path.join(tempfile.mkdtemp(), ".adapter")  # never the repo's own .adapter

import scan  # noqa: E402
from scanner import elm, honda, logger  # noqa: E402
from scanner.car import Car  # noqa: E402
from scanner.elm.session import Elm  # noqa: E402
from scanner.sim.elm import ElmEmulator  # noqa: E402
from scanner.sim.pilot import Pilot  # noqa: E402

CLEAN = "sim://pilot?clean&latency=0"
DEMO = "sim://pilot?latency=0"


class TestCar(unittest.TestCase):
    def setUp(self):
        self.car = Car.open(CLEAN)

    def tearDown(self):
        self.car.close()

    def test_finds_both_modules_despite_one_reply_per_broadcast(self):
        self.assertEqual(set(self.car.modules), {0x11, 0x1D})
        self.assertEqual(self.car.engine, 0x11)
        self.assertEqual(self.car.elm.protocol, "7")
        self.assertEqual(len(self.car.modules[0x11].pids), 42)

    def test_read_batches_six_pids_per_request(self):
        sent_before = len(self.car.elm.port.sim.sent)
        names = logger.SETS["drive"]
        v = self.car.read(names)
        obd = [c for c in self.car.elm.port.sim.sent[sent_before:] if not c.startswith("AT")]
        self.assertEqual(len(obd), 3)  # 18 PIDs, 6 per request
        self.assertTrue(all(v[n] is not None for n in names), v)
        self.assertEqual(v["RPM"] > 500, True)

    def test_one_missed_batch_does_not_end_batching(self):
        car = Car.open("sim://pilot?clean&latency=0")
        try:
            sim = car.elm.port.sim
            names = logger.SETS["noise"]  # six PIDs: one request
            sim.drop_rate = 1.0  # every multi-frame reply loses a frame, even after retries
            v = car.read(names)
            self.assertTrue(all(v[n] is not None for n in names), v)  # read one at a time instead
            sim.drop_rate = 0.0
            before = len(sim.sent)
            car.read(names)
            obd = [c for c in sim.sent[before:] if not c.startswith("AT")]
            self.assertEqual(len(obd), 1, obd)  # still batching
        finally:
            car.close()

    def test_batching_stops_after_several_misses_in_a_row(self):
        car = Car.open("sim://pilot?clean&latency=0")
        try:
            sim = car.elm.port.sim
            names = logger.SETS["noise"]
            sim.drop_rate = 1.0
            for _ in range(3):
                car.read(names)
            self.assertFalse(car.modules[car.engine].batch)
            sim.drop_rate = 0.0
            before = len(sim.sent)
            car.read(names)
            obd = [c for c in sim.sent[before:] if not c.startswith("AT")]
            self.assertEqual(len(obd), 6, obd)
        finally:
            car.close()

    def test_unsupported_names_are_none(self):
        v = self.car.read(["RPM", "AMBIANT_AIR_TEMP", "OIL_TEMP"])
        self.assertIsNone(v["AMBIANT_AIR_TEMP"])
        self.assertIsNone(v["OIL_TEMP"])
        self.assertIsNotNone(v["RPM"])

    def test_codes_and_status(self):
        for addr in (0x11, 0x1D):
            self.assertEqual(self.car.codes(addr, "stored"), [])
            self.assertEqual(self.car.codes(addr, "permanent"), [])
        self.assertEqual(self.car.status().incomplete, [])
        self.assertEqual([n for n, _ in self.car.status(0x1D).monitors], ["components"])

    def test_misfires_and_monitors(self):
        m = self.car.misfires()
        self.assertEqual(sorted(m), [1, 2, 3, 4, 5, 6])
        self.assertIn(0x01, self.car.mids())
        self.assertTrue(all(t.passed for t in self.car.monitor(0x01)))

    def test_honda_reads_go_to_the_right_module(self):
        atf, p, refused = self.car.honda(honda.BY_NAME["ATF_TEMP"])
        self.assertIsNotNone(atf)
        self.assertIsNone(refused)
        vcm = self.car.honda(honda.BY_NAME["VCM_CYLINDERS"])[0]
        self.assertIn(vcm, (3, 6))
        r = self.car.query("222201", 0x11)  # the ECM refuses the ATF read
        self.assertEqual(r.refused(), {0x11: 0x31})

    def test_identity(self):
        self.assertEqual(self.car.identity(0x1D)[0], "TCM-TransmisCtrl")
        self.assertEqual(len(self.car.vin()), 17)

    def test_query_is_read_only(self):
        for bad in ("2E0101", "04", "3101", "1003", "27"):
            with self.assertRaises(ValueError):
                self.car.query(bad, 0x11)


class TestQuirks(unittest.TestCase):
    def open(self, **kw):
        sim = ElmEmulator(Pilot(), **kw)

        class Port:
            def __init__(self):
                self.out = b""

            def write(self, data):
                self.out += sim.feed(data)

            def read(self, n):
                d, self.out = self.out[:n], self.out[n:]
                return d

            @property
            def in_waiting(self):
                return len(self.out)

            def reset_input_buffer(self):
                self.out = b""

            def close(self):
                pass

        e = Elm(Port())
        self.assertTrue(e.setup())
        return e, sim

    def test_dropped_frame_is_retried(self):
        e, sim = self.open(drop_rate=1.0, seed=3)
        r = e.request("222615", to=0x11, retries=1)  # every multi-frame reply loses a frame
        self.assertNotIn(0x11, r)
        self.assertEqual(r.broken, [0x11])
        sim.drop_rate = 0.5
        ok = sum(0x11 in e.request("222615", to=0x11, retries=3) for _ in range(10))
        self.assertGreaterEqual(ok, 8)

    def test_forced_wrong_protocol_is_not_connected(self):
        e, _ = self.open()
        self.assertFalse(e.setup(protocol="6"))

    def test_genuine_elm_classifies(self):
        e, _ = self.open(clone=False)
        self.assertEqual(e.at("STI"), "?")
        t, notes = elm.adapter.classify({"chip": "ELM327 v1.5", "stn": "?"}, "BLE")
        self.assertEqual(t, elm.REFERENCE["name"])
        self.assertFalse(any("OK to commands" in n for n in notes))

    def test_ok_to_sti_is_not_an_stn(self):
        t, notes = elm.adapter.classify({"chip": "ELM327 v1.5", "stn": "OK"}, "BLE")
        self.assertEqual(t, elm.REFERENCE["name"])
        self.assertTrue(any("OK to commands" in n for n in notes))
        self.assertEqual(elm.adapter.classify({"chip": "ELM327 v1.4b", "stn": "STN2120 v5.6.19"}, "USB/serial")[0], "STN")


class TestLogger(unittest.TestCase):
    def test_reconnects_into_the_same_file_and_stops_when_the_engine_does(self):
        car = Car.open(CLEAN)
        lg = logger.Logger(car, ["RPM", "SPEED"], reopen=lambda: Car.open(CLEAN), quiet=True)
        lg.retry_delay = 0
        n = [0]

        def stop(cells):
            n[0] += 1
            if n[0] == 3:
                lg.car.elm.port.broken = True  # the BLE link drops
            if n[0] == 6:
                lg.car.elm.port.sim.car.running = False  # engine off
            return cells.get("RPM") == 0

        with tempfile.TemporaryDirectory() as d:
            log = logger.CsvLog(lg.columns, "t", d)
            why = lg.run(log, None, stop_when=stop)
            log.close()
            with open(log.path, encoding="utf-8") as f:
                text = f.read()
        lg.car.close()
        self.assertEqual(why, "stopped")
        self.assertIn("link lost", text)
        self.assertIn("reconnected", text)
        self.assertTrue(text.strip().splitlines()[-1].split(",")[3] in ("0.0", "0"))


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            scan.main(list(argv))
        except SystemExit as e:
            if e.code not in (None, 0):
                out.write(f"\nEXIT {e.code}")
    return out.getvalue()


class TestCli(unittest.TestCase):
    def test_report(self):
        out = run_cli("--port", DEMO, "--report")
        for want in ("P3400", "INCOMPLETE  EVAP system", "cyl 3: this / last drive     12", "ATF F (candidate)",
                     "transmission (TCM) (1D)"):
            self.assertIn(want, out)

    def test_clean_codes(self):
        out = run_cli("--port", CLEAN, "--codes")
        self.assertIn("check engine light off", out)
        self.assertNotIn("INCOMPLETE", out)

    def test_eleven_bit_refused(self):
        self.assertIn("the car doesn't", run_cli("--port", CLEAN, "--protocol", "6", "--codes"))

    def test_log(self):
        with tempfile.TemporaryDirectory() as d:
            out = run_cli("--port", DEMO, "--seconds", "1", "--set", "noise", "--honda", "--tag", "t", "--log-dir", d)
            files = os.listdir(d)
            self.assertEqual(len(files), 1, out)
            with open(os.path.join(d, files[0]), encoding="utf-8") as f:
                rows = f.read().splitlines()
            self.assertTrue(rows[0].startswith("time,elapsed_s,marker,rpm,mph"))
            self.assertIn("ATF F (candidate)", rows[0])
            self.assertGreater(len(rows), 5)


if __name__ == "__main__":
    unittest.main()
