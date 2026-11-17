"""YF Scan: read and log a 2009-2015 Honda Pilot (YF3/YF4) through an ELM327 adapter.

Built around the ELM327 v1.5 BLE mini (elm.REFERENCE; ours is a ViaBecs). Other ELM327s work too.

Standard OBD2 only (python-OBD). Read-only except --clear-codes, which asks first.

    python3 yf/yf_scan.py --port DEMO --codes      simulated Pilot, to check the laptop side works
    python3 yf/yf_scan.py --codes                  MIL, stored + pending codes, readiness monitors
    python3 yf/yf_scan.py --supported              every Mode 01 PID the PCM answers
    python3 yf/yf_scan.py --misfires               Mode 06 misfire counters per cylinder
    python3 yf/yf_scan.py                          log a drive to yf/logs/ (Enter = noise marker)
    python3 yf/yf_scan.py --set noise              smaller, faster set for chasing a noise
    python3 yf/yf_scan.py --honda-probe            try the candidate Honda reads (ATF temp, VCM), raw bytes
    python3 yf/yf_scan.py --honda                  log a drive with the Honda candidates added

    python3 yf/yf_scan.py --adapters               list adapters on every transport (USB, Bluetooth, BLE)
    python3 yf/yf_scan.py --port ble --adapter-info   what the adapter says it is, and how it's connected

--port: auto (serial ports, then a BLE scan), DEMO, a serial path (/dev/ttyUSB0, /dev/rfcomm0, COM3),
ble (scan for one BLE adapter), ble:<address>, or socket://<ip>:<port> for a Wi-Fi adapter.
"""

import argparse
import csv
import datetime
import math
import os
import queue
import sys
import threading
import time

import obd

import elm
import honda

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# name -> (column label, display unit to convert to or None to keep python-OBD's unit)
PIDS = {
    "RPM": ("rpm", None),
    "SPEED": ("mph", "mph"),
    "ENGINE_LOAD": ("load %", None),
    "ABSOLUTE_LOAD": ("abs load %", None),
    "THROTTLE_POS": ("throttle %", None),
    "RELATIVE_THROTTLE_POS": ("rel throttle %", None),
    "ACCELERATOR_POS_D": ("pedal %", None),
    "COOLANT_TEMP": ("coolant F", "degF"),
    "INTAKE_TEMP": ("intake air F", "degF"),
    "AMBIANT_AIR_TEMP": ("ambient F", "degF"),
    "INTAKE_PRESSURE": ("MAP kPa", "kilopascal"),
    "BAROMETRIC_PRESSURE": ("baro kPa", "kilopascal"),
    "MAF": ("MAF g/s", "gps"),
    "TIMING_ADVANCE": ("timing deg", None),
    "SHORT_FUEL_TRIM_1": ("STFT1 %", None),
    "LONG_FUEL_TRIM_1": ("LTFT1 %", None),
    "SHORT_FUEL_TRIM_2": ("STFT2 %", None),
    "LONG_FUEL_TRIM_2": ("LTFT2 %", None),
    "FUEL_STATUS": ("fuel system", None),
    "COMMANDED_EQUIV_RATIO": ("cmd lambda", None),
    # Upstream sensors on this engine are expected to be wideband A/F sensors (current PIDs);
    # which numbers answer depends on the sensor layout, so all candidates are listed and
    # unsupported ones are dropped at connect.
    "O2_S1_WR_CURRENT": ("AF S1 mA", None),
    "O2_S2_WR_CURRENT": ("AF S2 mA", None),
    "O2_S3_WR_CURRENT": ("AF S3 mA", None),
    "O2_S4_WR_CURRENT": ("AF S4 mA", None),
    "O2_S5_WR_CURRENT": ("AF S5 mA", None),
    "O2_S6_WR_CURRENT": ("AF S6 mA", None),
    "O2_B1S2": ("O2 B1S2 V", None),
    "O2_B2S2": ("O2 B2S2 V", None),
    "CATALYST_TEMP_B1S1": ("cat B1 F", "degF"),
    "CATALYST_TEMP_B2S1": ("cat B2 F", "degF"),
    "CONTROL_MODULE_VOLTAGE": ("volts", None),
    "FUEL_LEVEL": ("fuel %", None),
    "RUN_TIME": ("run s", None),
}

SETS = {
    # Every row, about 1 row/s on a USB clone (BLE rate not measured yet). Good default for baseline and longevity logs.
    "drive": ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "COOLANT_TEMP", "INTAKE_TEMP",
              "INTAKE_PRESSURE", "MAF", "TIMING_ADVANCE", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
              "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "O2_S1_WR_CURRENT", "O2_S5_WR_CURRENT",
              "O2_B1S2", "O2_B2S2", "CONTROL_MODULE_VOLTAGE"],
    # Few PIDs so rows come fast: what you need to tell rpm-, speed- and load-linked noises apart.
    "noise": ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "TIMING_ADVANCE",
              "SHORT_FUEL_TRIM_1", "SHORT_FUEL_TRIM_2"],
    "all": list(PIDS),
}

LIVE_LINE = ["RPM", "SPEED", "ENGINE_LOAD", "COOLANT_TEMP", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
             "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "CONTROL_MODULE_VOLTAGE"]

STATUS_HELP = {
    obd.OBDStatus.ELM_CONNECTED: "Adapter answers but the car doesn't. Is it pushed fully into the port, key ON?",
    obd.OBDStatus.OBD_CONNECTED: "Adapter sees the bus but the PCM isn't answering. Key ON (or engine running)?",
}


# --- values -------------------------------------------------------------------

def as_cell(name, value):
    """Turn a python-OBD value into a CSV cell in our display units."""
    if value is None:
        return ""
    unit = PIDS.get(name, (None, None))[1]
    if hasattr(value, "magnitude"):
        try:
            mag = value.to(unit).magnitude if unit else value.magnitude
        except Exception:
            mag = value.magnitude
        return round(float(mag), 3)
    if isinstance(value, (tuple, list)):
        return " / ".join(str(v) for v in value if v)
    return str(value)


# --- connection ---------------------------------------------------------------

def open_car(port, timeout, fast, check_voltage=True, protocol=None, adapter_only=False):
    """Connect through any ELM327 transport (elm/). adapter_only: stop at the adapter, car optional."""
    if port.upper() == "DEMO":
        if adapter_only:
            sys.exit("DEMO has no adapter to describe.")
        return FakeCar(protocol or "6")
    try:
        ad = elm.connect(port, timeout=timeout, fast=fast, check_voltage=check_voltage, protocol=protocol)
    except elm.adapter.NotConnected as e:
        sys.exit(f"No adapter: {e}")
    conn = ad.conn
    elm.adapter.show(ad, verbose=adapter_only)
    st = conn.status()
    if adapter_only:
        print(f"Car: {st}" + ("" if st == obd.OBDStatus.CAR_CONNECTED else f". {STATUS_HELP.get(st, '')}"))
        return conn
    if st != obd.OBDStatus.CAR_CONNECTED:
        conn.close()
        sys.exit(f"Not connected ({st}). {STATUS_HELP.get(st, '')}")
    print(f"Connected: {conn.port_name()}, {conn.protocol_name()}")
    if honda.is_29bit(conn):
        honda.use_29bit_standard_headers()
    return conn


def query(conn, name):
    cmd = obd.commands[name] if isinstance(name, str) else name
    if not conn.supports(cmd):
        return None
    r = conn.query(cmd)
    return None if r.is_null() else r.value


def show_adapters():
    print("Looking for adapters (USB and bound Bluetooth ports, then a 6 s BLE scan)...")
    found = elm.find_adapters()
    for port, kind, desc in found:
        print(f"  {kind:18} {('--port ' + port) if port else '':32} {desc}")
    if not any(p for p, _, _ in found):
        print("Nothing that looks like an OBD adapter.")


# --- codes and readiness --------------------------------------------------------

def show_codes(conn):
    st = query(conn, "STATUS")
    if st is None:
        print("Status (PID 01 01): no answer")
    else:
        print(f"Check engine light: {'ON' if st.MIL else 'off'}    codes counted by PCM: {st.DTC_count}")
    for label, cmd in (("Stored codes", "GET_DTC"), ("Pending codes", "GET_CURRENT_DTC")):
        codes = query(conn, cmd)
        print(f"{label}:", "none" if not codes else "")
        for code, desc in codes or []:
            print(f"  {code}  {desc or '(no description in python-OBD; look it up for Honda)'}")
    if st is not None:
        print("Readiness monitors (emissions test needs these complete):")
        tests = [v for k, v in vars(st).items() if hasattr(v, "available") and v.available]
        for t in sorted(tests, key=lambda t: t.name):
            print(f"  {'complete  ' if t.complete else 'INCOMPLETE'}  {t.name}")
    for name, label in (("DISTANCE_SINCE_DTC_CLEAR", "km since codes cleared"),
                        ("WARMUPS_SINCE_DTC_CLEAR", "warm-ups since codes cleared"),
                        ("DISTANCE_W_MIL", "km driven with check engine light on")):
        v = query(conn, name)
        if v is not None:
            print(f"{label}: {as_cell(name, v)}")


def clear_codes(conn):
    print("Clearing codes also resets every readiness monitor and the learned fuel trims.\n"
          "The car will FAIL an emissions test until the monitors complete again (days of driving).\n"
          "Key ON, engine OFF.")
    if input("Type 'clear' to go ahead: ").strip().lower() != "clear":
        print("Not cleared.")
        return
    conn.query(obd.commands.CLEAR_DTC)
    print("Sent. Run --codes again to confirm they're gone.")


def show_supported(conn):
    names = sorted(c.name for c in conn.supported_commands if c.mode == 1)
    print(f"{len(names)} Mode 01 commands answered:")
    for n in names:
        mark = "" if n in PIDS else "   (not in yf_scan's PID table yet)"
        print(f"  {n}{mark}")


def show_misfires(conn):
    print("Mode 06 misfire counters (Honda numbering: 1-3 rear bank, 4-6 front bank (unverified)):")
    any_answer = False
    for cyl in range(1, 7):
        r = conn.query(obd.commands[f"MONITOR_MISFIRE_CYLINDER_{cyl}"], force=True)  # read-only; ask even if unlisted
        m = None if r.is_null() else r.value
        if m is None or len(m) == 0:
            print(f"  cyl {cyl}: no answer")
            continue
        any_answer = True
        cur = m["MISFIRE_COUNT"]
        avg = m["MISFIRE_AVERAGE"]
        fmt = lambda t: "-" if t.is_null() else f"{as_cell('', t.value)}"
        print(f"  cyl {cyl}: this/last drive {fmt(cur):>6}   10-drive average {fmt(avg):>6}")
    if not any_answer:
        print("  The PCM didn't answer Mode 06 misfire IDs. Some cars only fill these after a full drive cycle.")


# --- logging --------------------------------------------------------------------

class CsvLog:
    def __init__(self, names, labels, tag):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(LOG_DIR, f"yf_{stamp}{'_' + tag if tag else ''}.csv")
        self.names = names
        self.file = open(self.path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(["time", "elapsed_s", "marker"] + labels)
        self.t0 = time.monotonic()

    def write(self, cells, marker=""):
        now = datetime.datetime.now().isoformat(timespec="milliseconds")
        self.writer.writerow([now, f"{time.monotonic() - self.t0:.2f}", marker] + [cells.get(n, "") for n in self.names])
        self.file.flush()  # every row: a pulled plug loses one row, not the file

    def close(self):
        self.file.close()


def marker_thread(q):
    """Each line typed (or just Enter) becomes a marker on the next row."""
    def run():
        n = 0
        for line in sys.stdin:
            n += 1
            q.put(line.strip() or f"mark{n}")
    threading.Thread(target=run, daemon=True).start()


def log_drive(conn, names, seconds, tag, with_honda=False):
    skipped = [n for n in names if not conn.supports(obd.commands[n])]
    names = [n for n in names if n not in skipped]
    if not names:
        sys.exit("None of the requested PIDs are supported.")
    if skipped:
        print(f"Not answered by this PCM, skipped: {', '.join(skipped)}")
    extras = [(c, honda.command(c, honda.headers(conn)[0])) for c in honda.CANDIDATES] if with_honda else []
    log = CsvLog(names + [c["name"] for c, _ in extras], [PIDS[n][0] for n in names] + [c["label"] for c, _ in extras], tag)
    markers = queue.Queue()
    if sys.stdin.isatty():
        marker_thread(markers)
        print("Press Enter to drop a marker when you hear something; type a word first (e.g. 'whine') to label it.")
    print(f"Logging {len(names)} PIDs to {log.path}  (Ctrl+C to stop)", flush=True)
    stop_at = time.monotonic() + seconds if seconds else None
    rows = 0
    try:
        while stop_at is None or time.monotonic() < stop_at:
            cells = {n: as_cell(n, query(conn, n)) for n in names}
            for cand, cmd in extras:
                v = honda.value(cand, honda.query_raw(conn, cmd))
                cells[cand["name"]] = "" if v is None else v
            marker = []
            while not markers.empty():
                marker.append(markers.get())
            log.write(cells, ";".join(marker))
            rows += 1
            if marker:
                print(f"\n  marker '{';'.join(marker)}' at {time.monotonic() - log.t0:.1f} s")
            line = "  ".join(f"{PIDS[n][0]} {cells[n]}" for n in LIVE_LINE if n in cells)
            print(("\r" if sys.stdout.isatty() else "") + line + "   ", end="" if sys.stdout.isatty() else "\n", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        secs = time.monotonic() - log.t0
        print(f"\nSaved {log.path}: {rows} rows in {secs:.0f} s ({rows / max(secs, 1e-3):.2f} rows/s)")


# --- demo -----------------------------------------------------------------------

class FakeResponse:
    def __init__(self, value):
        self.value = value

    def is_null(self):
        return self.value is None


class FakeCar:
    """A made-up Pilot: cold start, warm-up, a bit of driving. Lets the laptop side be tested at home."""

    def __init__(self, protocol="6"):
        self.protocol = protocol
        self.t0 = time.monotonic()
        self.supported_commands = {obd.commands[n] for n in PIDS if n not in ("O2_S2_WR_CURRENT", "O2_S3_WR_CURRENT",
                                                                               "O2_S4_WR_CURRENT", "O2_S6_WR_CURRENT")}
        self.supported_commands |= {obd.commands[n] for n in ("STATUS", "GET_DTC", "GET_CURRENT_DTC",
                                                              "DISTANCE_SINCE_DTC_CLEAR", "WARMUPS_SINCE_DTC_CLEAR")}
        self.supported_commands |= {obd.commands[f"MONITOR_MISFIRE_CYLINDER_{c}"] for c in range(1, 7)}
        print(f"Adapter: {elm.REFERENCE['name']} (simulated)")
        print("Connected: DEMO (simulated Pilot, values are made up)")

    def supports(self, cmd):
        return cmd in self.supported_commands

    def close(self):
        pass

    def protocol_id(self):
        return self.protocol

    def query(self, cmd, force=False):
        time.sleep(0.02)
        t = time.monotonic() - self.t0
        Q = obd.Unit.Quantity
        speed = max(0.0, 40 * math.sin(t / 20)) if t > 10 else 0.0
        rpm = 700 + speed * 45 + 150 * math.sin(t)
        n = cmd.name
        if n.startswith("HONDA_"):
            if cmd.header != b"DA1DF1":  # like the '09 report: Honda reads answer only on 29-bit DA1DF1
                return FakeResponse(None)
            data = [0] * 60
            data[26] = 40 + 70 + int(t)  # ~158 F and climbing
            data[50] = 6
            return FakeResponse([[0x62] + list(bytes.fromhex(cmd.command.decode()[2:])) + data])
        if n == "STATUS":
            from obd.OBDResponse import Status, StatusTest
            st = Status()
            st.DTC_count = 1
            for name, done in (("MISFIRE_MONITORING", True), ("FUEL_SYSTEM_MONITORING", True),
                               ("COMPONENT_MONITORING", True), ("CATALYST_MONITORING", True),
                               ("EVAPORATIVE_SYSTEM_MONITORING", False), ("OXYGEN_SENSOR_MONITORING", True),
                               ("OXYGEN_SENSOR_HEATER_MONITORING", True), ("EGR_VVT_SYSTEM_MONITORING", True)):
                setattr(st, name, StatusTest(name, True, done))
            return FakeResponse(st)
        if n == "GET_DTC":
            return FakeResponse([("P3400", "Cylinder Deactivation System Bank 1 (demo)")])
        if n == "GET_CURRENT_DTC":
            return FakeResponse([])
        if n.startswith("MONITOR_MISFIRE_CYLINDER_"):
            from obd.OBDResponse import Monitor, MonitorTest
            cyl = int(n.rsplit("_", 1)[1])
            m = Monitor()
            for tid, name, val in ((0x0C, "MISFIRE_COUNT", 12 if cyl == 3 else 0), (0x0B, "MISFIRE_AVERAGE", 4 if cyl == 3 else 0)):
                mt = MonitorTest()
                mt.tid, mt.name, mt.desc, mt.value, mt.min, mt.max = tid, name, name, Q(val, "count"), Q(0, "count"), Q(65535, "count")
                m.add_test(mt)
            return FakeResponse(m)
        values = {
            "RPM": Q(rpm, "rpm"), "SPEED": Q(speed * 1.609, "kph"), "ENGINE_LOAD": Q(20 + speed / 2, "percent"),
            "THROTTLE_POS": Q(12 + speed / 3, "percent"), "COOLANT_TEMP": Q(min(90, 20 + t * 1.5), "degC"),
            "INTAKE_TEMP": Q(25, "degC"), "AMBIANT_AIR_TEMP": Q(18, "degC"), "INTAKE_PRESSURE": Q(32 + speed / 2, "kilopascal"),
            "BAROMETRIC_PRESSURE": Q(92, "kilopascal"), "MAF": Q(3 + speed / 3, "gps"), "TIMING_ADVANCE": Q(12 + speed / 4, "degree"),
            "SHORT_FUEL_TRIM_1": Q(2 * math.sin(t * 3), "percent"), "LONG_FUEL_TRIM_1": Q(3.1, "percent"),
            "SHORT_FUEL_TRIM_2": Q(2 * math.cos(t * 3), "percent"), "LONG_FUEL_TRIM_2": Q(6.3, "percent"),
            "FUEL_STATUS": ("Closed loop, using oxygen sensor feedback to determine fuel mix", ""),
            "COMMANDED_EQUIV_RATIO": Q(1.0, "ratio"), "O2_S1_WR_CURRENT": Q(0.02 * math.sin(t * 2), "milliampere"),
            "O2_S5_WR_CURRENT": Q(0.02 * math.cos(t * 2), "milliampere"), "O2_B1S2": Q(0.65, "volt"), "O2_B2S2": Q(0.62, "volt"),
            "CATALYST_TEMP_B1S1": Q(min(600, 50 + t * 8), "degC"), "CATALYST_TEMP_B2S1": Q(min(590, 50 + t * 8), "degC"),
            "CONTROL_MODULE_VOLTAGE": Q(14.1, "volt"), "FUEL_LEVEL": Q(55, "percent"), "RUN_TIME": Q(int(t), "second"),
            "ABSOLUTE_LOAD": Q(25, "percent"), "RELATIVE_THROTTLE_POS": Q(5, "percent"), "ACCELERATOR_POS_D": Q(15, "percent"),
            "DISTANCE_SINCE_DTC_CLEAR": Q(1234, "kilometer"), "WARMUPS_SINCE_DTC_CLEAR": Q(40, "count"),
        }
        return FakeResponse(values.get(n))


# --- main -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="auto", help="auto, DEMO, a serial path (/dev/ttyUSB0, /dev/rfcomm0), "
                    "ble, ble:<address>, or socket://<ip>:<port>")
    ap.add_argument("--adapters", action="store_true", help="list adapters on every transport (no connection), exit")
    ap.add_argument("--adapter-info", action="store_true", help="connect to the adapter only: chip, voltage, "
                    "link details (BLE: which GATT pipe), then exit. Works with the key off")
    ap.add_argument("--timeout", type=float, default=0.5, help="seconds to wait per reply (clones can be slow)")
    ap.add_argument("--fast", action="store_true", help="python-OBD fast mode; off by default, clones handle it badly")
    ap.add_argument("--no-voltage-check", action="store_true", help="skip the adapter's battery-voltage check "
                    "(some clones report it wrong and refuse to connect)")
    ap.add_argument("--protocol", help="force the OBD protocol instead of auto-detect: 6 = CAN 11-bit 500k, "
                    "7 = CAN 29-bit 500k (what owners used for Honda reads like ATF temp)")
    ap.add_argument("--codes", action="store_true", help="check engine light, stored/pending codes, readiness, then exit")
    ap.add_argument("--supported", action="store_true", help="list the Mode 01 PIDs the PCM answers, then exit")
    ap.add_argument("--misfires", action="store_true", help="Mode 06 misfire counters per cylinder, then exit")
    ap.add_argument("--honda-probe", action="store_true", help="try the candidate Honda Mode 22 reads, show raw bytes, exit")
    ap.add_argument("--honda", action="store_true", help="when logging, add the candidate Honda reads as columns")
    ap.add_argument("--clear-codes", action="store_true", help="erase codes (asks first; resets readiness monitors)")
    ap.add_argument("--set", choices=sorted(SETS), default="drive", help="which PIDs to log (default: drive)")
    ap.add_argument("--pids", help="comma-separated python-OBD names instead of --set, e.g. RPM,SPEED,COOLANT_TEMP")
    ap.add_argument("--seconds", type=float, help="stop logging after this many seconds")
    ap.add_argument("--tag", help="word added to the log filename, e.g. baseline or after-plugs")
    args = ap.parse_args()

    if args.adapters:
        show_adapters()
        return
    conn = open_car(args.port, args.timeout, args.fast, not args.no_voltage_check, args.protocol, args.adapter_info)
    try:
        if args.adapter_info:
            pass
        elif args.clear_codes:
            clear_codes(conn)
        elif args.codes:
            show_codes(conn)
        elif args.supported:
            show_supported(conn)
        elif args.misfires:
            show_misfires(conn)
        elif args.honda_probe:
            honda.probe(conn)
        else:
            names = [p.strip().upper() for p in args.pids.split(",")] if args.pids else SETS[args.set]
            unknown = [n for n in names if n not in PIDS]
            if unknown:
                sys.exit(f"Not in the PID table: {', '.join(unknown)}")
            log_drive(conn, names, args.seconds, args.tag, args.honda)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
