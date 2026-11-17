"""Mode 01 PID 01 (and 41): check engine light, code count, readiness monitors."""

CONTINUOUS = ["misfire", "fuel system", "components"]
# Spark-ignition monitors, in bit order of bytes C (supported) and D (incomplete).
SPARK = ["catalyst", "heated catalyst", "EVAP system", "secondary air", "A/C refrigerant",
         "O2 sensors", "O2 sensor heaters", "EGR/VVT"]
DIESEL = ["NMHC catalyst", "NOx/SCR", "reserved", "boost pressure", "reserved", "exhaust gas sensor",
          "PM filter", "EGR/VVT"]


class Status:
    def __init__(self, payload):
        """payload: 41 01 A B C D."""
        a, b, c, d = payload[2:6]
        self.mil = bool(a & 0x80)
        self.count = a & 0x7F
        self.diesel = bool(b & 0x08)
        self.monitors = []  # (name, complete)
        for i, name in enumerate(CONTINUOUS):
            if b & (1 << i):
                self.monitors.append((name, not (b & (0x10 << i))))
        for i, name in enumerate(DIESEL if self.diesel else SPARK):
            if c & (1 << i):
                self.monitors.append((name, not (d & (1 << i))))
        self.raw = bytes(payload[2:6])

    @property
    def incomplete(self):
        return [n for n, ok in self.monitors if not ok]

    def __repr__(self):
        return f"Status(mil={self.mil}, count={self.count}, incomplete={self.incomplete})"
