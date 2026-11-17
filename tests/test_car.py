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



class TestQuick(unittest.TestCase):
    """Quick mode: the fast values every row, the rest only when due, one extra request a row at most."""

    def setUp(self):
        self.car = Car.open(CLEAN)
        self.now = [100.0]
        self.sim = self.car.elm.port.sim

    def tearDown(self):
        self.car.close()

    def logger(self, names, honda=False):
        return logger.Logger(self.car, names, honda, quiet=True, quick=True, clock=lambda: self.now[0])

    def row(self, lg):
        before = len(self.sim.sent)
        cells = lg.row()
        sent = [c for c in self.sim.sent[before:] if not c.startswith("AT")]
        return cells, sent

    def test_slow_values_spread_over_rows_then_wait_their_turn(self):
        lg = self.logger(logger.SETS["drive"])
        fast = [n for n in lg.names if logger.quick_every(n) == 0]
        slow = [n for n in lg.names if n not in fast]
        self.assertEqual(sorted(fast), sorted(logger.SETS["noise"]))
        self.assertEqual(len(slow), 12)
        seen = set()
        for _ in range(2):  # 12 slower values, 6 a row
            cells, sent = self.row(lg)
            self.assertEqual(len(sent), 2, sent)
            self.assertTrue(all(cells[n] is not None for n in fast), cells)
            seen |= set(cells) - set(fast)
        self.assertEqual(seen, set(slow))
        cells, sent = self.row(lg)  # nothing slower due: one request
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(set(cells), set(fast))
        self.assertEqual(set(lg.latest), set(lg.names))  # screens still have every value
        self.now[0] += logger.QUICK_DEFAULT
        cells, sent = self.row(lg)
        self.assertEqual(len(sent), 2, sent)

    def test_once_values_are_read_once(self):
        lg = self.logger(["RPM", "WARMUPS_SINCE_DTC_CLEAR", "BAROMETRIC_PRESSURE"])
        cells, _ = self.row(lg)
        self.assertIsNotNone(cells["WARMUPS_SINCE_DTC_CLEAR"])
        self.now[0] += 3600
        cells, _ = self.row(lg)
        self.assertNotIn("WARMUPS_SINCE_DTC_CLEAR", cells)
        self.assertIn("BAROMETRIC_PRESSURE", cells)

    def test_vcm_every_row_atf_when_due(self):
        lg = self.logger(logger.SETS["noise"], honda=True)
        cells, sent = self.row(lg)
        self.assertIn("ATF_TEMP", cells)
        self.assertIn("VCM_CYLINDERS", cells)
        cells, sent = self.row(lg)
        self.assertEqual(set(cells), set(logger.SETS["noise"]) | {"VCM_CYLINDERS"})
        self.assertEqual(len(sent), 2, sent)  # six PIDs, VCM

    def test_csv_leaves_unread_cells_blank(self):
        lg = self.logger(["RPM", "COOLANT_TEMP"])
        with tempfile.TemporaryDirectory() as d:
            log = logger.CsvLog(lg.columns, None, d)
            n = [0]
            lg.run(log, None, stop_when=lambda cells: (n.__setitem__(0, n[0] + 1), n[0] == 3)[1])
            log.close()
            with open(log.path, encoding="utf-8") as f:
                rows = f.read().splitlines()[1:]
        self.assertEqual(len(rows), 3)
        self.assertNotEqual(rows[0].split(",")[4], "")  # coolant read on the first row
        self.assertEqual([r.split(",")[4] for r in rows[1:]], ["", ""])

    def test_summary_timeline_carries_slow_values(self):
        import summarize
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "q.csv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("time,elapsed_s,marker,rpm,coolant F\n")
                for i in range(70):
                    f.write(f"t,{i},,{700 + i},{'150' if i % 10 == 0 else ''}\n")
            text = summarize.summarize(p)
        timeline = text.split("Timeline")[1].split("\n\n")[0].splitlines()[2:]
        self.assertEqual(len(timeline), 4, timeline)  # 0, 30, 60 s and the last row
        self.assertTrue(all(ln.split()[2] == "150" for ln in timeline), timeline)

    def test_nothing_fast_waits_instead_of_writing_empty_rows(self):
        lg = logger.Logger(self.car, ["COOLANT_TEMP"], quiet=True, quick=True)
        with tempfile.TemporaryDirectory() as d:
            log = logger.CsvLog(lg.columns, None, d)
            lg.run(log, None, seconds=0.6)
            log.close()
        self.assertEqual(log.rows, 1)


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
