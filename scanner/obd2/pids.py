"""Mode 01 PIDs: what each one is, how many bytes it answers with, and how to decode it.

Formulas are SAE J1979's. Values come out in the units the CSV columns use (mph, deg F, kPa, %),
so a log reads the way the dash does. Names match python-OBD's (RPM, COOLANT_TEMP, ...) so the
--pids lists people already have keep working.

A PID can feed more than one name (0x34 gives both the A/F sensor's lambda and its current);
the reader asks for each PID once.
"""


def _u16(d):
    return d[0] * 256 + d[1]


def _f(c):
    return round(c * 9 / 5 + 32, 1)


def _pct(a):
    return round(a * 100 / 255, 1)


def _trim(a):
    return round((a - 128) * 100 / 128, 1)


FUEL_STATUS = {1: "OL", 2: "CL", 4: "OL-drive", 8: "OL-fault", 16: "CL-fault"}
FUEL_STATUS_HELP = ("CL = closed loop (using the O2/A-F sensors), OL = open loop (cold), OL-drive = open "
                    "loop for load or decel fuel cut, OL-fault = open loop because of a fault, CL-fault = "
                    "closed loop with a sensor fault")


def _fuel_status(d):
    parts = [FUEL_STATUS.get(d[0], f"{d[0]:02X}")]
    if len(d) > 1 and d[1]:
        parts.append(FUEL_STATUS.get(d[1], f"{d[1]:02X}"))
    return "/".join(parts)


class Pid:
    __slots__ = ("pid", "name", "label", "size", "decode", "desc")

    def __init__(self, pid, name, label, size, decode, desc=""):
        self.pid, self.name, self.label, self.size, self.decode, self.desc = pid, name, label, size, decode, desc

    def __repr__(self):
        return f"Pid(0x{self.pid:02X} {self.name})"


# Sizes for every PID 01-4F plus a few later ones, so a multi-PID reply can be split even when it
# carries a PID we don't decode. 0 = unknown length.
SIZES = {
    0x00: 4, 0x01: 4, 0x02: 2, 0x03: 2, 0x04: 1, 0x05: 1, 0x06: 1, 0x07: 1, 0x08: 1, 0x09: 1, 0x0A: 1,
    0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0E: 1, 0x0F: 1, 0x10: 2, 0x11: 1, 0x12: 1, 0x13: 1, 0x1C: 1, 0x1D: 1,
    0x1E: 1, 0x1F: 2, 0x20: 4, 0x21: 2, 0x22: 2, 0x23: 2, 0x2C: 1, 0x2D: 1, 0x2E: 1, 0x2F: 1, 0x30: 1,
    0x31: 2, 0x32: 2, 0x33: 1, 0x3C: 2, 0x3D: 2, 0x3E: 2, 0x3F: 2, 0x40: 4, 0x41: 4, 0x42: 2, 0x43: 2,
    0x44: 2, 0x45: 1, 0x46: 1, 0x47: 1, 0x48: 1, 0x49: 1, 0x4A: 1, 0x4B: 1, 0x4C: 1, 0x4D: 2, 0x4E: 2,
    0x4F: 4, 0x51: 1, 0x52: 1, 0x59: 2, 0x5A: 1, 0x5B: 1, 0x5C: 1, 0x5D: 2, 0x5E: 2, 0x60: 4,
    0x80: 4, 0xA0: 4, 0xC0: 4,
}
for _p in range(0x14, 0x1C):
    SIZES[_p] = 2  # narrowband O2: voltage + trim
for _p in range(0x24, 0x2C):
    SIZES[_p] = 4  # wideband O2: lambda + voltage
for _p in range(0x34, 0x3C):
    SIZES[_p] = 4  # wideband O2: lambda + current

TABLE = [
    Pid(0x03, "FUEL_STATUS", "fuel system", 2, _fuel_status, "fuel system status, bank 1 / bank 2"),
    Pid(0x04, "ENGINE_LOAD", "load %", 1, lambda d: _pct(d[0]), "calculated engine load"),
    Pid(0x05, "COOLANT_TEMP", "coolant F", 1, lambda d: _f(d[0] - 40), "engine coolant temperature"),
    Pid(0x06, "SHORT_FUEL_TRIM_1", "STFT1 %", 1, lambda d: _trim(d[0]), "short-term fuel trim, bank 1"),
    Pid(0x07, "LONG_FUEL_TRIM_1", "LTFT1 %", 1, lambda d: _trim(d[0]), "long-term fuel trim, bank 1"),
    Pid(0x08, "SHORT_FUEL_TRIM_2", "STFT2 %", 1, lambda d: _trim(d[0]), "short-term fuel trim, bank 2"),
    Pid(0x09, "LONG_FUEL_TRIM_2", "LTFT2 %", 1, lambda d: _trim(d[0]), "long-term fuel trim, bank 2"),
    Pid(0x0A, "FUEL_PRESSURE", "fuel kPa", 1, lambda d: d[0] * 3, "fuel pressure (gauge)"),
    Pid(0x0B, "INTAKE_PRESSURE", "MAP kPa", 1, lambda d: d[0], "intake manifold absolute pressure"),
    Pid(0x0C, "RPM", "rpm", 2, lambda d: round(_u16(d) / 4, 1), "engine speed"),
    Pid(0x0D, "SPEED", "mph", 1, lambda d: round(d[0] * 0.621371, 1), "vehicle speed"),
    Pid(0x0E, "TIMING_ADVANCE", "timing deg", 1, lambda d: d[0] / 2 - 64, "ignition timing advance, cyl 1"),
    Pid(0x0F, "INTAKE_TEMP", "intake air F", 1, lambda d: _f(d[0] - 40), "intake air temperature"),
    Pid(0x10, "MAF", "MAF g/s", 2, lambda d: round(_u16(d) / 100, 2), "mass air flow"),
    Pid(0x11, "THROTTLE_POS", "throttle %", 1, lambda d: _pct(d[0]), "throttle position"),
    Pid(0x15, "O2_B1S2", "O2 B1S2 V", 2, lambda d: round(d[0] / 200, 3), "downstream O2 voltage, bank 1"),
    Pid(0x19, "O2_B2S2", "O2 B2S2 V", 2, lambda d: round(d[0] / 200, 3), "downstream O2 voltage, bank 2"),
    Pid(0x1F, "RUN_TIME", "run s", 2, _u16, "seconds since engine start"),
    Pid(0x21, "DISTANCE_W_MIL", "mi with MIL", 2, lambda d: round(_u16(d) * 0.621371, 1),
        "distance driven with the check engine light on"),
    Pid(0x2C, "COMMANDED_EGR", "cmd EGR %", 1, lambda d: _pct(d[0]), "commanded EGR"),
    Pid(0x2D, "EGR_ERROR", "EGR error %", 1, lambda d: _trim(d[0]), "EGR error"),
    Pid(0x2E, "EVAPORATIVE_PURGE", "purge %", 1, lambda d: _pct(d[0]), "commanded evaporative purge"),
    Pid(0x2F, "FUEL_LEVEL", "fuel %", 1, lambda d: _pct(d[0]), "fuel tank level"),
    Pid(0x30, "WARMUPS_SINCE_DTC_CLEAR", "warm-ups since clear", 1, lambda d: d[0],
        "warm-ups since codes were cleared"),
    Pid(0x31, "DISTANCE_SINCE_DTC_CLEAR", "mi since clear", 2, lambda d: round(_u16(d) * 0.621371, 1),
        "distance since codes were cleared"),
    Pid(0x32, "EVAP_VAPOR_PRESSURE", "evap Pa", 2,
        lambda d: round((_u16(d) - 65536 if _u16(d) > 32767 else _u16(d)) / 4, 2), "EVAP system vapor pressure"),
    Pid(0x33, "BAROMETRIC_PRESSURE", "baro kPa", 1, lambda d: d[0], "barometric pressure"),
    Pid(0x3C, "CATALYST_TEMP_B1S1", "cat B1 F", 2, lambda d: _f(_u16(d) / 10 - 40), "catalyst temp, bank 1"),
    Pid(0x3D, "CATALYST_TEMP_B2S1", "cat B2 F", 2, lambda d: _f(_u16(d) / 10 - 40), "catalyst temp, bank 2"),
    Pid(0x42, "CONTROL_MODULE_VOLTAGE", "volts", 2, lambda d: round(_u16(d) / 1000, 2), "module supply voltage"),
    Pid(0x43, "ABSOLUTE_LOAD", "abs load %", 2, lambda d: round(_u16(d) * 100 / 255, 1), "absolute load"),
    Pid(0x44, "COMMANDED_EQUIV_RATIO", "cmd lambda", 2, lambda d: round(_u16(d) * 2 / 65536, 3),
        "commanded air-fuel equivalence ratio"),
    Pid(0x45, "RELATIVE_THROTTLE_POS", "rel throttle %", 1, lambda d: _pct(d[0]), "relative throttle position"),
    Pid(0x46, "AMBIANT_AIR_TEMP", "ambient F", 1, lambda d: _f(d[0] - 40), "ambient air temperature"),
    Pid(0x47, "THROTTLE_POS_B", "throttle B %", 1, lambda d: _pct(d[0]), "absolute throttle position B"),
    Pid(0x49, "ACCELERATOR_POS_D", "pedal %", 1, lambda d: _pct(d[0]), "accelerator pedal position D"),
    Pid(0x4A, "ACCELERATOR_POS_E", "pedal E %", 1, lambda d: _pct(d[0]), "accelerator pedal position E"),
    Pid(0x4C, "THROTTLE_ACTUATOR", "cmd throttle %", 1, lambda d: _pct(d[0]), "commanded throttle actuator"),
    Pid(0x5C, "OIL_TEMP", "oil F", 1, lambda d: _f(d[0] - 40), "engine oil temperature"),
]
# Wideband (A/F) sensors, 0x34-0x3B: sensor 1..8. Which sensor is where comes from PID 0x13
# (on the Pilot: sensor 1 = bank 1 upstream, sensor 5 = bank 2 upstream).
for _n in range(1, 9):
    _p = 0x33 + _n
    TABLE.append(Pid(_p, f"O2_S{_n}_WR_CURRENT", f"AF S{_n} mA", 4, lambda d: round(_u16(d[2:]) / 256 - 128, 3),
                     f"A/F sensor {_n} current (0 = stoichiometric, + lean, - rich)"))
    TABLE.append(Pid(_p, f"O2_S{_n}_WR_LAMBDA", f"AF S{_n} lambda", 4, lambda d: round(_u16(d) * 2 / 65536, 3),
                     f"A/F sensor {_n} equivalence ratio"))

# PIDs read by their own commands rather than logged, for --supported.
SPECIAL = {0x01: "status: light, code count, readiness (--codes)", 0x13: "which O2 sensors are fitted",
           0x1C: "which OBD standard the car meets", 0x41: "readiness for this drive cycle"}

BY_NAME = {p.name: p for p in TABLE}
BY_PID = {}
for _x in TABLE:
    BY_PID.setdefault(_x.pid, []).append(_x)

# Bank/sensor names for PID 0x13's bits (two banks of up to four sensors).
O2_LOCATIONS_13 = ["B1S1", "B1S2", "B1S3", "B1S4", "B2S1", "B2S2", "B2S3", "B2S4"]


def bitmask_pids(base, data):
    """The PIDs a 'supported PIDs' reply (PID base, 4 bytes) says are supported."""
    out = []
    for i in range(32):
        if data[i // 8] & (0x80 >> (i % 8)):
            out.append(base + i + 1)
    return out


def split_multi(payload):
    """A Mode 01 reply (41 pid data pid data ...) -> {pid: data}. Stops at a PID of unknown size."""
    out = {}
    i = 1  # skip 0x41
    while i < len(payload):
        pid = payload[i]
        n = SIZES.get(pid, 0)
        if not n or i + 1 + n > len(payload):
            break
        out[pid] = bytes(payload[i + 1:i + 1 + n])
        i += 1 + n
    return out


def decode(name, data):
    p = BY_NAME[name]
    try:
        return p.decode(data)
    except (IndexError, ZeroDivisionError):
        return None
