"""The session's reply parser and the OBD2 decoders, against replies captured from the real car.

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures import pilot_2011 as F  # noqa: E402
from scanner import honda  # noqa: E402
from scanner.elm.session import Elm  # noqa: E402
from scanner.obd2 import dtc, monitors, pids  # noqa: E402
from scanner.obd2.status import Status  # noqa: E402


def parser(protocol="7"):
    e = Elm(port=None)
    e.protocol = protocol
    return e


class TestParse(unittest.TestCase):
    def setUp(self):
        self.e = parser()

    def test_single_frames_per_module(self):
        r = self.e.parse(F.BOTH_0100)
        self.assertEqual(set(r), {0x11, 0x1D})
        self.assertEqual(r[0x11], bytes.fromhex("4100BFBFA893"))
        self.assertEqual(r.positive(0x01)[0x1D][:2], b"\x41\x00")

    def test_spaces_or_not(self):
        self.assertEqual(self.e.parse(F.ECM_0100)[0x11], self.e.parse([F.ECM_0100[0].replace(" ", "")])[0x11])

    def test_multi_frame(self):
        r = self.e.parse(F.ECM_222615)
        self.assertEqual(len(r[0x11]), 0x39)
        self.assertEqual(r[0x11][:3], b"\x62\x26\x15")
        self.assertFalse(r.broken)

    def test_missing_frame_is_flagged_not_decoded(self):
        r = self.e.parse(F.DROPPED_0908)
        self.assertNotIn(0x11, r)
        self.assertEqual(r.broken, [0x11])

    def test_ran_out_of_frames(self):
        r = self.e.parse(F.ECM_090A)  # announces 23 bytes, 20 arrive
        self.assertNotIn(0x11, r)
        self.assertIn(0x11, r.broken)

    def test_refusal(self):
        r = self.e.parse(F.ECM_222201)
        self.assertEqual(r.refused(), {0x11: 0x31})
        self.assertEqual(r.positive(0x22), {})

    def test_status_lines(self):
        self.assertEqual(self.e.parse(["NO DATA"]).status, "NO DATA")
        self.assertEqual(self.e.parse(["CAN ERROR"]).status, "CAN ERROR")
        r = self.e.parse(F.SEARCHING)
        self.assertIn(0x1D, r)

    def test_eleven_bit(self):
        r = parser("6").parse(["7E8 06 41 00 BE 3F A8 13", "7E9 06 41 00 98 18 80 01"])
        self.assertEqual(set(r), {0x7E8, 0x7E9})

    def test_headers(self):
        e = self.e
        self.assertEqual(e.header_for(None), "DB33F1")
        self.assertEqual(e.header_for(0x1D), "DA1DF1")
        e.protocol = "6"
        self.assertEqual(e.header_for(None), "7DF")
        self.assertEqual(e.header_for(0x7E8), "7E0")


class TestDecoders(unittest.TestCase):
    def setUp(self):
        self.e = parser()

    def test_supported_mask(self):
        got = pids.bitmask_pids(0, bytes.fromhex("BFBFA893"))
        for p in (0x01, 0x03, 0x04, 0x05, 0x06, 0x0C, 0x0D, 0x10, 0x11, 0x13, 0x15, 0x19, 0x1C, 0x1F, 0x20):
            self.assertIn(p, got)
        for p in (0x02, 0x0A, 0x12, 0x14, 0x16, 0x1A):
            self.assertNotIn(p, got)

    def test_multi_pid(self):
        p = self.e.parse(F.MULTI_010C0D05)[0x11]
        d = pids.split_multi(p)
        self.assertEqual(set(d), {0x0C, 0x0D, 0x05})
        self.assertEqual(pids.decode("RPM", d[0x0C]), 632.0)
        self.assertEqual(pids.decode("SPEED", d[0x0D]), 0.0)
        self.assertEqual(pids.decode("COOLANT_TEMP", d[0x05]), 177.8)  # 0x79 - 40 = 81 C

    def test_trims_and_af(self):
        self.assertEqual(pids.decode("SHORT_FUEL_TRIM_1", b"\x7e"), -1.6)
        self.assertEqual(pids.decode("LONG_FUEL_TRIM_1", b"\x7d"), -2.3)
        self.assertEqual(pids.decode("O2_S1_WR_CURRENT", bytes.fromhex("80008000")), 0.0)
        self.assertEqual(pids.decode("O2_S1_WR_LAMBDA", bytes.fromhex("80008000")), 1.0)
        self.assertEqual(pids.decode("FUEL_STATUS", b"\x02\x02"), "CL/CL")

    def test_status_engine(self):
        st = Status(self.e.parse(F.ECM_0101)[0x11])
        self.assertFalse(st.mil)
        self.assertEqual(st.count, 0)
        self.assertEqual([n for n, _ in st.monitors], ["misfire", "fuel system", "components", "catalyst",
                                                       "EVAP system", "O2 sensors", "O2 sensor heaters", "EGR/VVT"])
        self.assertEqual(st.incomplete, [])

    def test_status_incomplete(self):
        st = Status(bytes.fromhex("41018107E504"))
        self.assertTrue(st.mil)
        self.assertEqual(st.count, 1)
        self.assertEqual(st.incomplete, ["EVAP system"])

    def test_dtcs(self):
        self.assertEqual(dtc.parse(self.e.parse(F.NO_CODES)[0x11]), [])
        self.assertEqual(dtc.parse(bytes.fromhex("43020302D400")), ["P0302", "U1400"])
        self.assertEqual(dtc.code(bytes.fromhex("3400")), "P3400")
        self.assertIn("VCM", dtc.describe("P3400"))

    def test_misfire_monitor(self):
        tests = monitors.parse(self.e.parse(F.MISFIRE_06A2)[0x11])
        self.assertEqual([(t.mid, t.tid, t.value, t.unit) for t in tests],
                         [(0xA2, 0x0B, 0, "counts"), (0xA2, 0x0C, 0, "counts")])
        self.assertTrue(all(t.passed for t in tests))
        self.assertEqual(tests[1].name, "this / last drive cycle")

    def test_signed_scaling(self):
        t = monitors.Test(0x01, 0x83, 0x8D, 0xFFFB, 0x8000, 0x0349)
        self.assertAlmostEqual(t.value, -0.0195, places=3)
        self.assertTrue(t.passed)

    def test_honda_atf(self):
        p = self.e.parse(F.TCM_222201)[0x1D]
        self.assertEqual(len(honda.payload(p, honda.BY_NAME["ATF_TEMP"]["request"])), 36)
        self.assertEqual(honda.value(honda.BY_NAME["ATF_TEMP"], p), 145.4)  # 0x67 - 40 = 63 C

    def test_honda_vcm(self):
        p = self.e.parse(F.ECM_222615)[0x11]
        self.assertEqual(honda.value(honda.BY_NAME["VCM_CYLINDERS"], p), 6)

    def test_module_names(self):
        p = self.e.parse(F.TCM_090A)[0x1D]
        name = bytes(b for b in p[3:] if 32 <= b < 127).decode()
        self.assertEqual(name, "TCM-TransmisCtrl")


if __name__ == "__main__":
    unittest.main()
