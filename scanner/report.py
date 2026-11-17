"""What each read shows, as sections of short lines, for every front end: scan.py prints them,
tui.py styles them, the phone app (phone.py, android/) shows them as cards. Reports what the data
shows; no diagnosis.

A line has a tone ("" normal, "good", "warn", "bad", "dim") and an indent level (two spaces each
when printed). New reads go here (and in car.py), not in a front end.
"""

from . import honda
from .elm.session import nrc_text
from .obd2 import dtc, monitors, pids

TONES = ("", "good", "warn", "bad", "dim")

LIVE = ["RPM", "SPEED", "COOLANT_TEMP", "INTAKE_TEMP", "ENGINE_LOAD", "MAF", "FUEL_STATUS",
        "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2",
        "CONTROL_MODULE_VOLTAGE", "FUEL_LEVEL"]


def fmt(v):
    return "-" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


class Section:
    def __init__(self, title, lines=None):
        self.title = title
        self.lines = lines or []  # [(text, tone, indent)]

    def add(self, text, tone="", indent=0):
        self.lines.append((text, tone, indent))
        return self

    def text(self, title=True):
        body = "\n".join("  " * ind + t for t, _, ind in self.lines)
        return f"== {self.title} ==\n{body}" if title else body

    def as_dict(self):
        return {"title": self.title, "lines": [{"text": t, "tone": tone, "indent": ind} for t, tone, ind in self.lines]}

    def __repr__(self):
        return f"Section({self.title!r}, {len(self.lines)} lines)"


# --- connection -------------------------------------------------------------------

def adapter(ad):
    """What the adapter says it is, and the link it's on."""
    from .elm.adapter import IDENT, REFERENCE
    i = ad.ident
    s = Section("Adapter")
    s.add(f"{i.get('chip') or '?'} over {ad.kind}, type: {i.get('type', '?')}",
          "good" if i.get("type") in (REFERENCE["name"], "STN") else "warn")
    for key, cmd in IDENT:
        s.add(f"{cmd:5} {i.get(key) or '(no answer)'}", indent=1)
    if ad.link is not None:
        for line in ad.link.info():
            s.add(line, "dim", 1)
    for n in i.get("notes", []):
        s.add(f"note: {n}", "dim", 1)
    return s


def connection(car):
    """One line on the adapter, then whether the car answers: protocol, battery, modules."""
    ad = car.adapter
    s = Section("Connection")
    s.add(f"Adapter: {ad.ident.get('chip') or '?'} over {ad.kind}, type: {ad.ident.get('type', '?')}")
    if not car.connected:
        return s.add("Adapter answers, the car doesn't. Key ON (or engine running)? Adapter pushed fully in?", "bad")
    v = car.elm.voltage()
    s.add(f"Car: {car.elm.protocol_name}{f', {v:.1f} V at the port' if v else ''}", "good")
    s.add("Modules: " + ", ".join(f"{a:02X} {m.name}" for a, m in car.modules.items()))
    return s


# --- codes and readiness ----------------------------------------------------------

def codes(car):
    s = Section("Codes and readiness")
    for addr, m in car.modules.items():
        st = car.status(addr)
        head = f"{m.name} ({addr:02X})"
        if st is None:
            s.add(f"{head}: status (PID 01 01): no answer", "warn")
        else:
            light = f"check engine light {'ON' if st.mil else 'off'}, " if addr == car.engine else ""
            s.add(f"{head}: {light}{st.count} code(s) counted", "bad" if st.mil else "")
        for kind in ("stored", "pending", "permanent"):
            found = car.codes(addr, kind)
            if found is None:
                s.add(f"{kind}: no answer", "warn", 1)
            elif not found:
                s.add(f"{kind}: none", "good", 1)
            else:
                s.add(f"{dtc.MODES[kind][1]}:", "bad", 1)
                for c in found:
                    s.add(f"{c}  {dtc.describe(c)}", "bad", 2)
        if st is not None and st.monitors:
            s.add("readiness monitors (an emissions test needs these complete):", indent=1)
            for name, ok in st.monitors:
                s.add(f"{'complete  ' if ok else 'INCOMPLETE'}  {name}", "good" if ok else "warn", 2)
    extra = car.read(["DISTANCE_SINCE_DTC_CLEAR", "WARMUPS_SINCE_DTC_CLEAR", "DISTANCE_W_MIL"])
    units = {"DISTANCE_SINCE_DTC_CLEAR": " mi", "DISTANCE_W_MIL": " mi", "WARMUPS_SINCE_DTC_CLEAR": ""}
    for name, v in extra.items():
        if v is not None:
            s.add(f"{pids.BY_NAME[name].desc}: {fmt(v)}{units[name]}", "dim")
    return s


CLEAR_WARNING = ("Clearing codes also resets every readiness monitor and the learned fuel trims.\n"
                 "The car will FAIL an emissions test until the monitors complete again (days of driving).\n"
                 "Permanent codes stay until the car confirms the fix itself. Key ON, engine OFF.")


def clear_codes(car):
    """Sends Mode 04 to every module. Only after the person has typed 'clear' (front ends ask)."""
    ok = car.clear_codes()
    names = ", ".join(car.modules[a].name if a in car.modules else f"{a:02X}" for a in sorted(ok))
    s = Section("Clear codes")
    s.add(f"Sent. Confirmed by: {names or 'nobody (read the codes to check)'}.", "good" if ok else "warn")
    s.add("Readiness monitors are reset: the car fails an emissions test until they complete again (days of driving).", "warn")
    return s.add("Read the codes again to see what's left.", "dim")


# --- misfires, Mode 06, live ------------------------------------------------------

def misfires(car):
    m = car.misfires()
    s = Section("Misfires")
    s.add("Mode 06 misfire counters, engine module (Honda numbering: 1-3 rear bank, 4-6 front bank (unverified)):", "dim")
    if not m:
        return s.add("The engine module didn't answer the misfire monitors. Some cars only fill these after a full "
                     "drive cycle.", "warn", 1)
    for cyl in range(1, 7):
        if cyl not in m:
            s.add(f"cyl {cyl}: no answer", "warn", 1)
            continue
        cur = m[cyl].get("this / last drive cycle")
        avg = m[cyl].get("average (last 10 drive cycles)")
        s.add(f"cyl {cyl}: this / last drive {fmt(cur):>6}    10-drive average {fmt(avg):>6}", "warn" if cur else "", 1)
    counts = {c: m[c].get("this / last drive cycle") or 0 for c in m}
    if counts and max(counts.values()) > 0:
        worst = max(counts, key=counts.get)
        s.add(f"Highest: cylinder {worst}. Uneven counts on one cylinder are worth following up (plug, coil, injector).",
              "warn", 1)
    return s


def mode06(car, failed_only=False):
    """Every Mode 06 result (or only those outside their limits). Returns (section, how many failed)."""
    s = Section("Mode 06 tests outside their limits" if failed_only else "Mode 06 results")
    mids = sorted(car.mids())
    if not mids:
        return s.add("No Mode 06 monitors answered.", "warn"), 0
    bad = 0
    for mid in mids:
        tests = car.monitor(mid)
        if tests is None:
            if not failed_only:
                s.add(f"{monitors.mid_name(mid)}: no answer", "warn", 1)
            continue
        for t in tests:
            bad += not t.passed
            if failed_only and t.passed:
                continue
            s.add(f"{monitors.mid_name(mid):24} {t.name:30} {fmt(t.value):>9} {t.unit:12} "
                  f"limits {fmt(t.min)}..{fmt(t.max)}  {'pass' if t.passed else 'FAIL'}", "" if t.passed else "bad", 1)
    if failed_only and not bad:
        s.add("none: every test result is within the limits the engine computer set for it", "good", 1)
    return s, bad


def live(car):
    v = car.read(LIVE)
    s = Section("Live values (one snapshot)")
    for n in LIVE:
        if v[n] is not None:
            s.add(f"{pids.BY_NAME[n].label:16} {fmt(v[n])}", indent=1)
    for b in ("1", "2"):
        st, lt = v.get(f"SHORT_FUEL_TRIM_{b}"), v.get(f"LONG_FUEL_TRIM_{b}")
        if st is not None and lt is not None:
            s.add(f"bank {b} total trim {st + lt:+.1f} %  (sustained beyond about +/-10 % is worth a look; "
                  "one snapshot isn't sustained)", "warn" if abs(st + lt) > 10 else "", 1)
    return s


def honda_reads(car):
    """The Honda candidates, decoded. None when not on 29-bit CAN (where the Pilot answers them)."""
    if not car.elm.is_29bit:
        return None
    s = Section("Honda reads (candidate decodes, cross-check before trusting)")
    for cand in honda.CANDIDATES:
        val, _, refused = car.honda(cand)
        s.add(f"{cand['label']:28} {fmt(val) if refused is None else nrc_text(refused)}",
              "" if refused is None else "warn", 1)
    return s


def health(car, each=None):
    """The --report health check, section by section. each(section) sees each one as it's read."""
    out = []
    for read in (codes, misfires, lambda c: mode06(c, failed_only=True)[0], live, honda_reads):
        s = read(car)
        if s is not None:
            out.append(s)
            if each:
                each(s)
    return out


# --- info -------------------------------------------------------------------------

def vehicle(car):
    s = Section("Vehicle")
    s.add(f"VIN: {car.vin() or '(no answer)'}")
    for addr, m in car.modules.items():
        name, cals = car.identity(addr)
        s.add(f"{addr:02X}: {name or m.name}" + (f"   calibration {', '.join(cals)}" if cals else ""))
    return s


def supported(car):
    s = Section("Supported PIDs")
    for addr, m in car.modules.items():
        s.add(f"{m.name} ({addr:02X}): {len(m.pids)} Mode 01 PIDs")
        for p in sorted(m.pids):
            known = pids.BY_PID.get(p)
            names = ", ".join(x.name for x in known) if known else pids.SPECIAL.get(p, "(not in yf's PID table)")
            s.add(f"{p:02X}  {names}", "" if known else "dim", 1)
    locs = car.o2_locations()
    if locs:
        s.add(f"O2 sensors fitted (PID 13): {', '.join(locs)}")
    return s


def honda_probe(car):
    s = Section("Honda probe")
    s.add("Honda enhanced candidates (read-only Mode 22). Raw bytes shown so the decode can be checked.", "dim")
    if not car.elm.is_29bit:
        return s.add("Not on 29-bit CAN, where the Pilot's modules answer Honda reads. Try without --protocol.", "warn")
    for cand in honda.CANDIDATES:
        addr = cand["module"]
        v, p, refused = car.honda(cand)
        tag = f"{cand['name']} ({cand['request'].hex().upper()} to {addr:02X} {honda.module_name(addr)})"
        if p is None:
            s.add(f"{tag}: no answer", "warn", 1)
            continue
        s.add(f"{tag}: {len(p)} bytes: {p.hex(' ').upper()}", indent=1)
        if refused is not None:
            s.add(f"refused, {nrc_text(refused)}", "warn", 2)
            continue
        data = honda.payload(p, cand["request"])
        if data is None:
            s.add("not a positive reply to this request (a different 62 xx xx echo means the adapter answered the "
                  "wrong request, a known cheap-clone fault)", "bad", 2)
            continue
        i = honda.torque_index(cand["byte"])
        s.add(f"{len(data)} data bytes; byte {cand['byte']} (index {i}) -> {fmt(v)} {cand['label']}", indent=2)
    return s


# --- one read-only request --------------------------------------------------------

def query(car, hexreq, module):
    """One read-only request to one module: every raw line, then the payload.
    Raises ValueError for an unknown module, bad hex, or a service that isn't a read."""
    addr = car.module(module)
    if addr is None:
        raise ValueError(f"No module '{module}' answered.")
    r = car.query(hexreq, addr)
    s = Section(f"{hexreq.upper()} to {addr:02X} {honda.module_name(addr)}")
    for ln in r.lines:
        s.add(f"< {ln}", "dim", 1)
    p = r.get(addr)
    if p is None:
        s.add(f"{addr:02X}: {r.status or 'no answer'}" + (" (frames missing)" if addr in r.broken else ""), "warn", 1)
    elif p[0] == 0x7F:
        s.add(f"{addr:02X}: {nrc_text(p[2]) if len(p) > 2 else p.hex(' ')}", "warn", 1)
    else:
        s.add(f"{addr:02X}: {len(p)} bytes: {p.hex(' ').upper()}", "good", 1)
    return s
