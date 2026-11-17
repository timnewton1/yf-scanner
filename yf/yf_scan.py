"""YF Scan: read and log a 2009-2015 Honda Pilot (YF3/YF4) through an ELM327 adapter.

Built around the ELM327 v1.5 BLE mini (elm.REFERENCE; ours is a ViaBecs). Other ELM327s work too.
Read-only except --clear-codes, which asks first.

    yf_scan.py --port DEMO --report       simulated Pilot, to check the laptop side works
    yf_scan.py --adapters                 list adapters on every transport (USB, Bluetooth, BLE)
    yf_scan.py --adapter-info             what the adapter says it is, and how it's connected
    yf_scan.py --report                   health check: codes in every module, readiness, misfires,
                                          Mode 06 results that failed, live values, Honda reads
    yf_scan.py --codes                    check engine light, stored / pending / permanent codes, readiness
    yf_scan.py --misfires                 Mode 06 misfire counters per cylinder
    yf_scan.py --monitors                 every Mode 06 test result with its limits
    yf_scan.py --supported                every Mode 01 PID each module answers
    yf_scan.py --info                     VIN, module names, calibration IDs
    yf_scan.py --honda-probe              the Honda Mode 22 candidates (ATF temp, VCM), raw bytes
    yf_scan.py                            log a drive to yf/logs/ (Enter = noise marker)
    yf_scan.py --set noise --honda        smaller, faster set for chasing a noise, plus Honda reads
    yf_scan.py --unattended               Raspberry Pi: log every drive, one file each

--port: auto (the saved adapter, else serial ports, then a BLE scan), DEMO, a serial path (/dev/ttyUSB0, /dev/rfcomm0, COM3),
ble (scan for one BLE adapter), ble:<address>, or socket://<ip>:<port> for a Wi-Fi adapter.
The first adapter that answers is saved in .adapter (gitignored), so after that no --port is needed.
"""

import argparse
import sys

import elm
import honda
import logger
from car import READ_ONLY_SERVICES, Car, ElmError
from elm.session import nrc_text
from obd2 import dtc, monitors, pids


# --- connection -----------------------------------------------------------------

def opener(args):
    """A callable that opens the car with the command-line settings (logging reuses it to reconnect)."""
    log = (lambda d, t: print(f"    {d} {t}", file=sys.stderr)) if args.raw else None

    def open_car():
        return Car.open(args.port, timeout=args.timeout, protocol=args.protocol, baud=args.baud, log=log)
    return open_car


def open_car(args, adapter_only=False):
    try:
        if adapter_only:
            ad = elm.connect(args.port, timeout=args.timeout, protocol=args.protocol, baud=args.baud)
            elm.adapter.show(ad, verbose=True)
            print(f"Car: {'answers on ' + ad.elm.protocol_name if ad.car else 'not answering (key off is fine for this)'}")
            ad.close()
            return None
        car = opener(args)()
    except elm.NotConnected as e:
        sys.exit(f"No adapter: {e}")
    elm.adapter.show(car.adapter)
    if not car.connected:
        car.close()
        sys.exit("Adapter answers, the car doesn't. Key ON (or engine running)? Adapter pushed fully in? "
                 + ("" if not args.protocol else f"Protocol {args.protocol} forced: try without --protocol."))
    v = car.elm.voltage()
    mods = ", ".join(f"{a:02X} {m.name}" for a, m in car.modules.items())
    print(f"Car: {car.elm.protocol_name}{f', {v:.1f} V at the port' if v else ''}; modules: {mods}")
    return car


def show_adapters():
    print("Looking for adapters (USB and Bluetooth ports, then a 6 s BLE scan)...")
    found = elm.find_adapters()
    for port, kind, desc in found:
        print(f"  {kind:18} {('--port ' + port) if port else '':32} {desc}")
    if not any(p for p, _, _ in found):
        print("Nothing that looks like an OBD adapter.")
    if elm.saved():
        print(f"Saved adapter (used by default): {elm.saved()}  (--forget-adapter to drop it)")


# --- codes and readiness ----------------------------------------------------------

def show_codes(car):
    for addr, m in car.modules.items():
        st = car.status(addr)
        head = f"{m.name} ({addr:02X})"
        if st is None:
            print(f"{head}: status (PID 01 01): no answer")
        else:
            light = f"check engine light {'ON' if st.mil else 'off'}, " if addr == car.engine else ""
            print(f"{head}: {light}{st.count} code(s) counted")
        for kind in ("stored", "pending", "permanent"):
            codes = car.codes(addr, kind)
            if codes is None:
                print(f"  {kind}: no answer")
            elif not codes:
                print(f"  {kind}: none")
            else:
                print(f"  {dtc.MODES[kind][1]}:")
                for c in codes:
                    print(f"    {c}  {dtc.describe(c)}")
        if st is not None and st.monitors:
            print("  readiness monitors (an emissions test needs these complete):")
            for name, ok in st.monitors:
                print(f"    {'complete  ' if ok else 'INCOMPLETE'}  {name}")
    extra = car.read(["DISTANCE_SINCE_DTC_CLEAR", "WARMUPS_SINCE_DTC_CLEAR", "DISTANCE_W_MIL"])
    units = {"DISTANCE_SINCE_DTC_CLEAR": " mi", "DISTANCE_W_MIL": " mi", "WARMUPS_SINCE_DTC_CLEAR": ""}
    for name, v in extra.items():
        if v is not None:
            print(f"{pids.BY_NAME[name].desc}: {fmt(v)}{units[name]}")


def clear_codes(car):
    print("Clearing codes also resets every readiness monitor and the learned fuel trims.\n"
          "The car will FAIL an emissions test until the monitors complete again (days of driving).\n"
          "Permanent codes stay until the car confirms the fix itself. Key ON, engine OFF.")
    if input("Type 'clear' to go ahead: ").strip().lower() != "clear":
        print("Not cleared.")
        return
    ok = car.clear_codes()
    names = ", ".join(car.modules[a].name if a in car.modules else f"{a:02X}" for a in sorted(ok))
    print(f"Sent. Confirmed by: {names or 'nobody (run --codes to check)'}. Run --codes to see what's left.")


# --- supported, misfires, monitors, info --------------------------------------------

def show_supported(car):
    for addr, m in car.modules.items():
        print(f"{m.name} ({addr:02X}): {len(m.pids)} Mode 01 PIDs")
        for p in sorted(m.pids):
            known = pids.BY_PID.get(p)
            names = ", ".join(x.name for x in known) if known else pids.SPECIAL.get(p, "(not in yf's PID table)")
            print(f"  {p:02X}  {names}")
    locs = car.o2_locations()
    if locs:
        print(f"O2 sensors fitted (PID 13): {', '.join(locs)}")


def show_misfires(car):
    m = car.misfires()
    print("Mode 06 misfire counters, engine module (Honda numbering: 1-3 rear bank, 4-6 front bank (unverified)):")
    if not m:
        print("  The engine module didn't answer the misfire monitors. Some cars only fill these after a full drive cycle.")
        return
    for cyl in range(1, 7):
        if cyl not in m:
            print(f"  cyl {cyl}: no answer")
            continue
        cur = m[cyl].get("this / last drive cycle")
        avg = m[cyl].get("average (last 10 drive cycles)")
        print(f"  cyl {cyl}: this / last drive {fmt(cur):>6}    10-drive average {fmt(avg):>6}")
    counts = [m[c].get("this / last drive cycle") or 0 for c in m]
    if counts and max(counts) > 0:
        worst = max(m, key=lambda c: m[c].get("this / last drive cycle") or 0)
        print(f"  Highest: cylinder {worst}. Uneven counts on one cylinder are worth following up (plug, coil, injector).")


def fmt(v):
    return "-" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def show_monitors(car, failed_only=False):
    mids = sorted(car.mids())
    if not mids:
        print("No Mode 06 monitors answered.")
        return 0
    bad = 0
    for mid in mids:
        tests = car.monitor(mid)
        if tests is None:
            if not failed_only:
                print(f"  {monitors.mid_name(mid)}: no answer")
            continue
        for t in tests:
            ok = t.passed
            bad += not ok
            if failed_only and ok:
                continue
            print(f"  {monitors.mid_name(mid):24} {t.name:30} {fmt(t.value):>9} {t.unit:12} "
                  f"limits {fmt(t.min)}..{fmt(t.max)}  {'pass' if ok else 'FAIL'}")
    return bad


def show_info(car):
    print(f"VIN: {car.vin() or '(no answer)'}")
    for addr, m in car.modules.items():
        name, cals = car.identity(addr)
        print(f"{addr:02X}: {name or m.name}" + (f"   calibration {', '.join(cals)}" if cals else ""))


# --- Honda ------------------------------------------------------------------------

def honda_probe(car):
    print("Honda enhanced candidates (read-only Mode 22). Raw bytes shown so the decode can be checked.")
    if not car.elm.is_29bit:
        print("Not on 29-bit CAN, where the Pilot's modules answer Honda reads. Try without --protocol.")
        return
    for cand in honda.CANDIDATES:
        addr = cand["module"]
        v, p, refused = car.honda(cand)
        tag = f"{cand['name']} ({cand['request'].hex().upper()} to {addr:02X} {honda.module_name(addr)})"
        if p is None:
            print(f"  {tag}: no answer")
            continue
        print(f"  {tag}: {len(p)} bytes: {p.hex(' ').upper()}")
        if refused is not None:
            print(f"    refused, {nrc_text(refused)}")
            continue
        data = honda.payload(p, cand["request"])
        if data is None:
            print("    not a positive reply to this request (a different 62 xx xx echo means the adapter "
                  "answered the wrong request, a known cheap-clone fault)")
            continue
        i = honda.torque_index(cand["byte"])
        print(f"    {len(data)} data bytes; byte {cand['byte']} (index {i}) -> {fmt(v)} {cand['label']}")


# --- report -----------------------------------------------------------------------

REPORT_LIVE = ["RPM", "SPEED", "COOLANT_TEMP", "INTAKE_TEMP", "ENGINE_LOAD", "MAF", "FUEL_STATUS",
               "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2",
               "CONTROL_MODULE_VOLTAGE", "FUEL_LEVEL"]


def report(car):
    print("\n== Codes and readiness ==")
    show_codes(car)
    print("\n== Misfires ==")
    show_misfires(car)
    print("\n== Mode 06 tests outside their limits ==")
    bad = show_monitors(car, failed_only=True)
    if bad == 0:
        print("  none: every test result is within the limits the engine computer set for it")
    print("\n== Live values (one snapshot) ==")
    v = car.read(REPORT_LIVE)
    for n in REPORT_LIVE:
        if v[n] is not None:
            print(f"  {pids.BY_NAME[n].label:16} {fmt(v[n])}")
    for b in ("1", "2"):
        s, lt = v.get(f"SHORT_FUEL_TRIM_{b}"), v.get(f"LONG_FUEL_TRIM_{b}")
        if s is not None and lt is not None:
            print(f"  bank {b} total trim {s + lt:+.1f} %  (sustained beyond about +/-10 % is worth a look; "
                  "one snapshot isn't sustained)")
    if car.elm.is_29bit:
        print("\n== Honda reads (candidate decodes, cross-check before trusting) ==")
        for cand in honda.CANDIDATES:
            val, p, refused = car.honda(cand)
            print(f"  {cand['label']:28} {fmt(val) if refused is None else nrc_text(refused)}")


# --- raw read-only query ----------------------------------------------------------

def query(car, hexreq, module):
    addr = car.module(module)
    if addr is None:
        sys.exit(f"No module '{module}' answered.")
    try:
        r = car.query(hexreq, addr)
    except ValueError as e:
        sys.exit(str(e))
    for ln in r.lines:
        print(f"  < {ln}")
    p = r.get(addr)
    if p is None:
        print(f"  {addr:02X}: {r.status or 'no answer'}" + (" (frames missing)" if addr in r.broken else ""))
    elif p[0] == 0x7F:
        print(f"  {addr:02X}: {nrc_text(p[2]) if len(p) > 2 else p.hex(' ')}")
    else:
        print(f"  {addr:02X}: {len(p)} bytes: {p.hex(' ').upper()}")


# --- main -------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="auto", help="auto (default: the saved adapter, else look), DEMO, a serial path (/dev/ttyUSB0, /dev/rfcomm0, COM3), "
                    "ble, ble:<address>, or socket://<ip>:<port>")
    ap.add_argument("--adapters", action="store_true", help="list adapters on every transport (no connection), exit")
    ap.add_argument("--forget-adapter", action="store_true", help="delete the saved adapter (.adapter), exit")
    ap.add_argument("--adapter-info", action="store_true", help="the adapter's chip, voltage reading and link "
                    "(BLE: which GATT pipe), then exit. Works with the key off")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait per reply (default 1.0)")
    ap.add_argument("--baud", type=int, help="USB/serial adapters: skip the baud-rate probe")
    ap.add_argument("--protocol", help="force the ELM327 protocol instead of auto-detect (7 = CAN 29-bit 500k, "
                    "what the Pilot uses; 6 = CAN 11-bit 500k, which it doesn't answer)")
    ap.add_argument("--raw", action="store_true", help="print every line sent to and received from the adapter")
    ap.add_argument("--report", action="store_true", help="health check: codes, readiness, misfires, failed "
                    "Mode 06 tests, live values, Honda reads")
    ap.add_argument("--codes", action="store_true", help="check engine light, codes and readiness in every module")
    ap.add_argument("--supported", action="store_true", help="the Mode 01 PIDs each module answers")
    ap.add_argument("--misfires", action="store_true", help="Mode 06 misfire counters per cylinder")
    ap.add_argument("--monitors", action="store_true", help="every Mode 06 test result, with limits and pass/fail")
    ap.add_argument("--info", action="store_true", help="VIN, module names, calibration IDs")
    ap.add_argument("--modules", action="store_true", help="list the modules that answer OBD requests")
    ap.add_argument("--sweep", action="store_true", help="with --modules: also try every address (slow, read-only)")
    ap.add_argument("--honda-probe", action="store_true", help="the Honda Mode 22 candidates, with raw bytes")
    ap.add_argument("--query", metavar="HEX", help="send one read-only request (services "
                    + ", ".join(f"{s:02X}" for s in sorted(READ_ONLY_SERVICES)) + ") and print the reply, e.g. 0902")
    ap.add_argument("--module", default="engine", help="for --query: engine, transmission, or a hex address (1D)")
    ap.add_argument("--clear-codes", action="store_true", help="erase codes (asks first; resets readiness monitors)")
    ap.add_argument("--set", choices=sorted(logger.SETS), default="drive", help="which PIDs to log (default: drive)")
    ap.add_argument("--pids", help="comma-separated names instead of --set, e.g. RPM,SPEED,COOLANT_TEMP")
    ap.add_argument("--honda", action="store_true", help="when logging, add the Honda candidate reads as columns")
    ap.add_argument("--seconds", type=float, help="stop logging after this many seconds")
    ap.add_argument("--tag", help="word added to the log filename, e.g. baseline or after-plugs")
    ap.add_argument("--unattended", action="store_true", help="Raspberry Pi mode: wait for the engine, log each "
                    "drive to its own file, repeat forever")
    ap.add_argument("--gpio-button", type=int, metavar="PIN", help="with --unattended: GPIO pin of a marker button")
    ap.add_argument("--log-dir", help="where logs go (default yf/logs)")
    args = ap.parse_args(argv)

    if args.forget_adapter:
        prev = elm.saved()
        print(f"Forgot {prev}; the next connect looks for an adapter again." if elm.forget() else "No saved adapter.")
        return
    if args.adapters:
        show_adapters()
        return
    if args.adapter_info:
        open_car(args, adapter_only=True)
        return

    names = [p.strip().upper() for p in args.pids.split(",")] if args.pids else logger.SETS[args.set]
    unknown = [n for n in names if n not in pids.BY_NAME]
    if unknown:
        sys.exit(f"Not in the PID table: {', '.join(unknown)}. Known: {', '.join(sorted(pids.BY_NAME))}")

    if args.unattended:
        try:
            logger.unattended(opener(args), names, args.honda, args.gpio_button, args.tag or "auto",
                              directory=args.log_dir)
        except KeyboardInterrupt:
            pass
        return

    car = open_car(args)
    try:
        if args.clear_codes:
            clear_codes(car)
        elif args.report:
            report(car)
        elif args.codes:
            show_codes(car)
        elif args.supported:
            show_supported(car)
        elif args.misfires:
            show_misfires(car)
        elif args.monitors:
            show_monitors(car)
        elif args.info:
            show_info(car)
        elif args.modules:
            if args.sweep:
                print("Sweeping every address with a read-only 01 00 (about 40 s)...")
                car.discover(sweep=True)
            for a, m in car.modules.items():
                name, _ = car.identity(a)
                print(f"  {a:02X}  {m.name:20} {name or '':20} {len(m.pids)} Mode 01 PIDs")
        elif args.honda_probe:
            honda_probe(car)
        elif args.query:
            query(car, args.query, args.module)
        else:
            logger.drive(car, names, args.seconds, args.tag, args.honda, reopen=opener(args), directory=args.log_dir)
            return  # drive() closes the car (it may have reconnected to a new one)
    except ElmError as e:
        sys.exit(f"Lost the adapter: {e}")
    finally:
        try:
            car.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
