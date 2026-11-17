"""A 2011 Honda Pilot on the OBD port, modeled on what the real one answered on 2026-11-16
(supported-PID masks, module names, Mode 06 layout, Mode 22 reply sizes). Values move with a
made-up drive: cold start, warm-up, a few minutes of stop-and-go.

    11  ECM: engine PIDs, Mode 06 (O2, catalyst, EGR, EVAP, misfire per cylinder), VIN, 22 2615
    1D  TCM: a few PIDs, its own codes, 22 2201 (ATF temp candidate at byte AA)

Everything else gets the refusal the real modules give (7F 22 31 for an unknown identifier,
7F xx 11 for a service they don't do). 11-bit requests get NO DATA, as on the car.
"""

import math
import time

from obd2 import pids as P

ECM, TCM = 0x11, 0x1D

SUPPORTED_01 = {  # base -> 4 mask bytes, exactly as captured
    ECM: {0x00: "BFBFA893", 0x20: "801FF119", 0x40: "FAD00000"},
    TCM: {0x00: "983A8003", 0x20: "8000A001", 0x40: "CAC00000"},
}
SUPPORTED_06 = {ECM: {0x00: "CC000001", 0x20: "C0008059", 0x40: "00000001", 0x60: "00000001",
                      0x80: "00000001", 0xA0: "7E000000"}}
SUPPORTED_09 = {ECM: {0x00: "55400000"}, TCM: {0x00: "14400000"}}
NAMES = {ECM: "ECM\x00-EngineControl", TCM: "TCM\x00-TransmisCtrl"}
CAL_IDS = {ECM: "37805-RN0-A830", TCM: "37806-RN0-A820"}
VIN = "5FNYF4H00SIM00001"  # not a real car's VIN

# Mode 06 groups for the O2 / catalyst / EGR / EVAP monitors: (TID, UASID, value, min, max).
# MID 01 and 05 are from the real car; the rest are plausible passing results.
MONITORS = {
    0x01: [(0x80, 0x11, 0x0068, 0, 0x0190), (0x87, 0x14, 0x004B, 0, 0x010E), (0x82, 0x0B, 0x01CA, 0x00F9, 0x02EF),
           (0x88, 0x0B, 0x01CA, 0x00F9, 0x02EF), (0x83, 0x8D, 0xFFFB, 0x8000, 0x0349), (0x84, 0x8D, 0x041C, 0x0137, 0x058E),
           (0x85, 0x01, 0x06C6, 0x0022, 0xFFFF), (0x86, 0x8D, 0xFFF8, 0x8000, 0x05FD)],
    0x02: [(0x07, 0x0B, 0x00A0, 0, 0x0190), (0x08, 0x0B, 0x02F0, 0x0258, 0xFFFF)],
    0x05: [(0x80, 0x11, 0x0068, 0, 0x0190), (0x87, 0x14, 0x004B, 0, 0x010E), (0x82, 0x0B, 0x01CA, 0x00F9, 0x02EF),
           (0x88, 0x0B, 0x01CA, 0x00F9, 0x02EF), (0x83, 0x8D, 0xFFED, 0x8000, 0x0346), (0x84, 0x8D, 0x03A5, 0x0134, 0x058B),
           (0x85, 0x01, 0x0564, 0x0022, 0xFFFF), (0x86, 0x8D, 0xFFFE, 0x8000, 0x05FA)],
    0x06: [(0x07, 0x0B, 0x00A8, 0, 0x0190), (0x08, 0x0B, 0x02E4, 0x0258, 0xFFFF)],
    0x21: [(0x80, 0x01, 0x0040, 0, 0x00C0)],
    0x22: [(0x80, 0x01, 0x0046, 0, 0x00C0)],
    0x31: [(0xD0, 0x32, 0x0360, 0, 0x0524), (0xD1, 0x32, 0x0954, 0x00C1, 0xFFFF)],
    0x3A: [(0xC0, 0xFE, 0x0010, 0x8000, 0x0100)],
    0x3C: [(0xC1, 0xFE, 0x0020, 0x8000, 0x0100)],
    0x3D: [(0xB9, 0x30, 0xFFFF, 0x4CCE, 0xFFFF)],
}

# Mode 22 2615 reply from the ECM: 54 data bytes, active cylinders at index 50 (Torque "AY").
VCM_TEMPLATE = bytes.fromhex("000000" "0000F800000000" + "00" * 35 + "00770003000600" "0000")
# Mode 22 2201 reply from the TCM: 36 data bytes; index 26 (AA) = ATF + 40 C, index 20 = coolant + 40 C.
ATF_TEMPLATE = bytes.fromhex("FFFFFF" "8000000002BF00" "00029F08235000" "32192779281789" "5C4E678B000000" "0000000000")
assert len(VCM_TEMPLATE) == 54 and len(ATF_TEMPLATE) == 36


class Pilot:
    protocol = "7"

    def __init__(self, demo=False, clock=time.monotonic, start_coolant_c=20.0):
        self.clock = clock
        self.t0 = clock()
        self.start_coolant = start_coolant_c
        self.modules = [ECM, TCM]
        self.codes = {ECM: {"stored": [], "pending": [], "permanent": []},
                      TCM: {"stored": [], "pending": [], "permanent": []}}
        self.misfire = {c: (0, 0) for c in range(1, 7)}  # cylinder -> (this cycle, average)
        self.evap_incomplete = False
        self.running = True  # False: key on, engine off (rpm 0)
        if demo:  # something to look at in DEMO output
            self.codes[ECM]["stored"] = ["P3400"]
            self.misfire[3] = (12, 4)
            self.evap_incomplete = True

    # --- the made-up drive ------------------------------------------------------------

    def state(self):
        t = self.clock() - self.t0
        mph = max(0.0, 40 * math.sin((t - 10) / 20)) if t > 10 and self.running else 0.0
        rpm = 700 + mph * 30 + 120 * math.sin(t) if self.running else 0.0
        coolant = min(88.0, self.start_coolant + t * 1.5)
        throttle = 12 + mph / 3
        steady = 30 < mph < 60 and abs(math.cos((t - 10) / 20)) < 0.5
        return {
            "t": t, "mph": mph, "kph": mph / 0.621371, "rpm": rpm, "coolant": coolant, "iat": 25.0,
            "load": 20 + mph / 2, "throttle": throttle, "map": 32 + mph / 2, "maf": 3 + mph / 3,
            "timing": 12 + mph / 4, "stft1": 2 * math.sin(t * 3), "stft2": 2 * math.cos(t * 3),
            "ltft1": -2.3, "ltft2": 1.6, "volts": 14.1 if rpm > 400 else 12.6,
            "atf": min(85.0, self.start_coolant + 40 + t * 0.4), "cylinders": 3 if steady else 6,
        }

    def battery_volts(self):
        return self.state()["volts"] - 0.3

    # --- answering --------------------------------------------------------------------

    def respond(self, m, req):
        if not req:
            return None
        s = req[0]
        if s == 0x01:
            return self.mode01(m, req[1:])
        if s in (0x03, 0x07, 0x0A):
            kind = {0x03: "stored", 0x07: "pending", 0x0A: "permanent"}[s]
            if len(req) != 1:
                return bytes([0x7F, s, 0x13])
            codes = self.codes[m][kind]
            body = b"".join(_dtc_bytes(c) for c in codes)
            return bytes([s + 0x40, len(codes)]) + body
        if s == 0x04:
            for k in self.codes[m]:
                if k != "permanent":
                    self.codes[m][k] = []
            self.misfire = {c: (0, 0) for c in range(1, 7)}
            self.evap_incomplete = True
            return b"\x44"
        if s == 0x06:
            return self.mode06(m, req[1:]) if m == ECM else None
        if s == 0x09:
            return self.mode09(m, req[1:])
        if s == 0x22:
            return self.mode22(m, req[1:])
        return bytes([0x7F, s, 0x11])

    def mode01(self, m, pids):
        if not pids or len(pids) > 6:
            return None
        out = bytearray([0x41])
        for p in pids:
            if p % 0x20 == 0:
                mask = SUPPORTED_01[m].get(p)
                if mask is None:
                    continue
                out += bytes([p]) + bytes.fromhex(mask)
                continue
            if not self._supports(SUPPORTED_01[m], p):
                continue
            data = self.encode(m, p)
            if data is not None:
                out += bytes([p]) + data
        return bytes(out) if len(out) > 1 else None

    @staticmethod
    def _supports(masks, p):
        base = (p - 1) // 0x20 * 0x20
        mask = masks.get(base)
        return bool(mask) and p in P.bitmask_pids(base, bytes.fromhex(mask))

    def encode(self, m, p):
        s = self.state()
        b1 = lambda v: bytes([max(0, min(255, int(round(v))))])
        b2 = lambda v: int(max(0, min(65535, round(v)))).to_bytes(2, "big")
        pct = lambda v: b1(v * 255 / 100)
        trim = lambda v: b1(v * 128 / 100 + 128)
        if p == 0x01:
            ecm = self.codes[ECM]
            n = len(self.codes[m]["stored"])
            mil = 0x80 if (m == ECM and ecm["stored"]) else 0
            if m == ECM:
                return bytes([mil | n, 0x07, 0xE5, 0x04 if self.evap_incomplete else 0x00])
            return bytes([n, 0x04, 0x00, 0x00])
        if p == 0x41:
            return bytes.fromhex("0007E1A5") if m == ECM else bytes.fromhex("00040000")
        table = {
            0x03: lambda: bytes([2 if s["coolant"] > 40 else 1, 2 if s["coolant"] > 40 else 1]),
            0x04: lambda: pct(s["load"]), 0x05: lambda: b1(s["coolant"] + 40),
            0x06: lambda: trim(s["stft1"]), 0x07: lambda: trim(s["ltft1"]),
            0x08: lambda: trim(s["stft2"]), 0x09: lambda: trim(s["ltft2"]),
            0x0B: lambda: b1(s["map"]), 0x0C: lambda: b2(s["rpm"] * 4), 0x0D: lambda: b1(s["kph"]),
            0x0E: lambda: b1((s["timing"] + 64) * 2), 0x0F: lambda: b1(s["iat"] + 40),
            0x10: lambda: b2(s["maf"] * 100), 0x11: lambda: pct(s["throttle"]),
            0x13: lambda: b"\x33", 0x15: lambda: bytes([130, 0xFF]), 0x19: lambda: bytes([124, 0xFF]),
            0x1C: lambda: b"\x01", 0x1F: lambda: b2(s["t"]), 0x21: lambda: b2(0),
            0x2C: lambda: b1(0), 0x2D: lambda: b1(128), 0x2E: lambda: pct(10), 0x2F: lambda: pct(55),
            0x30: lambda: b1(40), 0x31: lambda: b2(1234), 0x32: lambda: b2(0), 0x33: lambda: b1(99),
            0x34: lambda: b2(32768) + b2((s["stft1"] / 50 + 128) * 256),
            0x38: lambda: b2(32768) + b2((s["stft2"] / 50 + 128) * 256),
            0x3C: lambda: b2((min(600, 50 + s["t"] * 8) + 40) * 10),
            0x3D: lambda: b2((min(590, 50 + s["t"] * 8) + 40) * 10),
            0x42: lambda: b2(s["volts"] * 1000), 0x43: lambda: b2(s["load"] * 1.1 * 255 / 100),
            0x44: lambda: b2(32768), 0x45: lambda: pct(s["throttle"] - 10), 0x47: lambda: pct(s["throttle"] + 4),
            0x49: lambda: pct(15 + s["mph"] / 3), 0x4A: lambda: pct(7 + s["mph"] / 6), 0x4C: lambda: pct(s["throttle"]),
        }
        f = table.get(p)
        return f() if f else None

    def mode06(self, m, req):
        if len(req) != 1:
            return None
        mid = req[0]
        if mid % 0x20 == 0:
            mask = SUPPORTED_06[m].get(mid)
            return bytes([0x46, mid]) + bytes.fromhex(mask) if mask else None
        if 0xA2 <= mid <= 0xA7:
            cur, avg = self.misfire[mid - 0xA1]
            groups = [(0x0B, 0x24, avg, 0, 0xFFFF), (0x0C, 0x24, cur, 0, 0xFFFF)]
        else:
            groups = MONITORS.get(mid)
            if groups is None:
                return None
        out = bytearray([0x46])
        for tid, uas, v, lo, hi in groups:
            out += bytes([mid, tid, uas]) + v.to_bytes(2, "big") + lo.to_bytes(2, "big") + hi.to_bytes(2, "big")
        return bytes(out)

    def mode09(self, m, req):
        if len(req) != 1:
            return None
        pid = req[0]
        if pid == 0x00:
            return bytes([0x49, 0x00]) + bytes.fromhex(SUPPORTED_09[m][0x00])
        if pid == 0x02 and m == ECM:
            return bytes([0x49, 0x02, 0x01]) + VIN.encode()
        if pid == 0x04:
            return bytes([0x49, 0x04, 0x01]) + CAL_IDS[m].encode().ljust(16, b"\x00")
        if pid == 0x06:
            return bytes([0x49, 0x06, 0x01]) + (b"\x1a\x2b\x3c\x4d" if m == ECM else b"\x5e\x6f\x70\x81")
        if pid == 0x0A:
            return bytes([0x49, 0x0A, 0x01]) + NAMES[m].encode().ljust(20, b"\x00")
        return None

    def mode22(self, m, req):
        if len(req) != 2:
            return bytes([0x7F, 0x22, 0x13])
        did = req[0] << 8 | req[1]
        s = self.state()
        if m == ECM and did == 0x2615:
            data = bytearray(VCM_TEMPLATE)
            data[50] = s["cylinders"]
            return b"\x62\x26\x15" + bytes(data)
        if m == TCM and did == 0x2201:
            data = bytearray(ATF_TEMPLATE)
            data[26] = int(round(s["atf"] + 40))
            data[20] = int(round(s["coolant"] + 40))
            return b"\x62\x22\x01" + bytes(data)
        return bytes([0x7F, 0x22, 0x31])


def _dtc_bytes(c):
    first = "PCBU".index(c[0]) << 6 | int(c[1]) << 4 | int(c[2], 16)
    return bytes([first, int(c[3:5], 16)])
