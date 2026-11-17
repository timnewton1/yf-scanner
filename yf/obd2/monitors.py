"""Mode 06: on-board monitor test results (CAN format), each with the limits the car judges it by.

A reply is 46 <MID> then 9-byte groups: MID, TID, unit-and-scaling ID, value, min, max (2 bytes
each). Test IDs 0x80 and up are manufacturer-defined: Honda doesn't publish what they measure, so
they're shown by number with their units and pass/fail.

Scaling IDs from SAE J1979 Appendix E. Ones not listed here are shown raw, marked "(raw)".
"""

MIDS = {
    0x01: "O2 sensor B1S1", 0x02: "O2 sensor B1S2", 0x03: "O2 sensor B1S3", 0x04: "O2 sensor B1S4",
    0x05: "O2 sensor B2S1", 0x06: "O2 sensor B2S2", 0x07: "O2 sensor B2S3", 0x08: "O2 sensor B2S4",
    0x21: "catalyst bank 1", 0x22: "catalyst bank 2", 0x23: "catalyst bank 3", 0x24: "catalyst bank 4",
    0x31: "EGR bank 1", 0x32: "EGR bank 2", 0x35: "VVT bank 1", 0x36: "VVT bank 2",
    0x39: "EVAP 0.150\" (cap off)", 0x3A: "EVAP 0.090\"", 0x3B: "EVAP 0.040\"", 0x3C: "EVAP 0.020\"",
    0x3D: "EVAP purge flow",
    0x41: "O2 heater B1S1", 0x42: "O2 heater B1S2", 0x43: "O2 heater B1S3", 0x44: "O2 heater B1S4",
    0x45: "O2 heater B2S1", 0x46: "O2 heater B2S2", 0x47: "O2 heater B2S3", 0x48: "O2 heater B2S4",
    0x61: "heated catalyst bank 1", 0x62: "heated catalyst bank 2",
    0x71: "secondary air 1", 0x72: "secondary air 2",
    0x81: "fuel system bank 1", 0x82: "fuel system bank 2",
    0xA1: "misfire, general", **{0xA1 + n: f"misfire cylinder {n}" for n in range(1, 13)},
}

# Standard test IDs for misfire MIDs
MISFIRE_TIDS = {0x0B: "average (last 10 drive cycles)", 0x0C: "this / last drive cycle"}

# UASID -> (multiplier, offset, unit, signed)
UAS = {
    0x01: (1, 0, "", False), 0x02: (0.1, 0, "", False), 0x03: (0.01, 0, "", False),
    0x04: (0.001, 0, "", False), 0x05: (0.0000305, 0, "", False), 0x06: (0.000305, 0, "", False),
    0x07: (0.25, 0, "rpm", False), 0x08: (0.01, 0, "km/h", False), 0x09: (1, 0, "km/h", False),
    0x0A: (0.122, 0, "mV", False), 0x0B: (0.001, 0, "V", False), 0x0C: (0.01, 0, "V", False),
    0x0D: (0.00390625, 0, "mA", False), 0x0E: (0.001, 0, "A", False), 0x0F: (0.01, 0, "A", False),
    0x10: (1, 0, "ms", False), 0x11: (100, 0, "ms", False), 0x12: (1, 0, "s", False),
    0x13: (1, 0, "mOhm", False), 0x14: (1, 0, "Ohm", False), 0x15: (1, 0, "kOhm", False),
    0x16: (0.1, -40, "C", False), 0x17: (0.01, 0, "kPa", False), 0x1A: (1, 0, "kPa", False),
    0x1C: (0.01, 0, "deg", False), 0x1D: (0.5, 0, "deg", False), 0x1E: (0.0000305, 0, "lambda", False),
    0x20: (0.0039062, 0, "ratio", False), 0x22: (1, 0, "Hz", False), 0x24: (1, 0, "counts", False),
    0x25: (1, 0, "km", False), 0x27: (0.01, 0, "g/s", False), 0x28: (1, 0, "g/s", False),
    0x2F: (0.01, 0, "%", False),
    0x81: (1, 0, "", True), 0x82: (0.1, 0, "", True), 0x83: (0.01, 0, "", True), 0x84: (0.001, 0, "", True),
    0x85: (0.0000305, 0, "", True), 0x86: (0.000305, 0, "", True), 0x8A: (0.122, 0, "mV", True),
    0x8B: (0.001, 0, "V", True), 0x8C: (0.01, 0, "V", True), 0x8D: (0.00390625, 0, "mA", True),
    0x8E: (0.001, 0, "A", True), 0x90: (1, 0, "ms", True), 0x96: (0.1, 0, "C", True),
    0x9C: (0.01, 0, "deg", True), 0x9D: (0.5, 0, "deg", True), 0xA8: (1, 0, "g/s", True),
    0xA9: (0.25, 0, "Pa/s", True), 0xAF: (0.01, 0, "%", True), 0xB0: (0.003052, 0, "%", True),
    0xFC: (0.01, 0, "kPa", True), 0xFD: (0.001, 0, "kPa", True), 0xFE: (0.25, 0, "Pa", True),
}


class Test:
    __slots__ = ("mid", "tid", "uas", "value", "min", "max", "unit", "scaled")

    def __init__(self, mid, tid, uas, raw_v, raw_min, raw_max):
        self.mid, self.tid, self.uas = mid, tid, uas
        mult, off, unit, signed = UAS.get(uas, (1, 0, "", False))
        self.scaled = uas in UAS

        def s(x):
            if signed and x > 32767:
                x -= 65536
            return round(x * mult + off, 4)

        self.value, self.min, self.max = s(raw_v), s(raw_min), s(raw_max)
        self.unit = unit if self.scaled else f"(raw, scaling 0x{uas:02X})"

    @property
    def passed(self):
        return self.min <= self.value <= self.max

    @property
    def name(self):
        if 0xA1 <= self.mid <= 0xAD and self.tid in MISFIRE_TIDS:
            return MISFIRE_TIDS[self.tid]
        return f"test {self.tid:02X}" + (" (Honda)" if self.tid >= 0x80 else "")


def parse(payload):
    """46 MID ... -> [Test]."""
    body = payload[1:]
    out = []
    for i in range(0, len(body) - 8, 9):
        g = body[i:i + 9]
        out.append(Test(g[0], g[1], g[2], g[3] * 256 + g[4], g[5] * 256 + g[6], g[7] * 256 + g[8]))
    return out


def mid_name(mid):
    return MIDS.get(mid, f"monitor {mid:02X}")
