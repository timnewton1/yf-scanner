"""YF Scan: read and log a 2009-2015 Honda Pilot (YF3/YF4) through an ELM327 adapter.

Built around the ELM327 v1.5 BLE mini (elm.REFERENCE; ours is a ViaBecs). Other ELM327s work too.
Read-only except --clear-codes, which asks first.

    scan.py --port DEMO --report       simulated Pilot, to check the laptop side works
    scan.py --adapters                 list adapters on every transport (USB, Bluetooth, BLE)
    scan.py --adapter-info             what the adapter says it is, and how it's connected
    scan.py --report                   health check: codes in every module, readiness, misfires,
                                          Mode 06 results that failed, live values, Honda reads
    scan.py --codes                    check engine light, stored / pending / permanent codes, readiness
    scan.py --misfires                 Mode 06 misfire counters per cylinder
    scan.py --monitors                 every Mode 06 test result with its limits
    scan.py --supported                every Mode 01 PID each module answers
    scan.py --info                     VIN, module names, calibration IDs
    scan.py --honda-probe              the Honda Mode 22 candidates (ATF temp, VCM), raw bytes
    scan.py                            log a drive to logs/ (Enter = noise marker)
    scan.py --set noise --honda        smaller, faster set for chasing a noise, plus Honda reads
    scan.py --quick --honda            the drive set, but only the noise values every row (fast)
    scan.py --unattended               Raspberry Pi: log every drive, one file each

--port: auto (the saved adapter, else serial ports, then a BLE scan), DEMO, a serial path (/dev/ttyUSB0, /dev/rfcomm0, COM3),
ble (scan for one BLE adapter), ble:<address>, or socket://<ip>:<port> for a Wi-Fi adapter.
The first adapter that answers is saved in .adapter (gitignored), so after that no --port is needed.
"""

import argparse
import sys

from scanner import elm, logger, report
from scanner.car import READ_ONLY_SERVICES, Car, ElmError
from scanner.obd2 import pids


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


# --- reads: each is a section from scanner/report.py, printed ----------------------

def show(section):
    print(section.text(title=False))


def clear_codes(car):
    print(report.CLEAR_WARNING)
    if input("Type 'clear' to go ahead: ").strip().lower() != "clear":
        print("Not cleared.")
        return
    show(report.clear_codes(car))


def health(car):
    for s in report.health(car):
        print(f"\n{s.text()}")


def query(car, hexreq, module):
    try:
        show(report.query(car, hexreq, module))
    except ValueError as e:
        sys.exit(str(e))


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
    ap.add_argument("--quick", action="store_true", help="when logging, read the noise values (and VCM) every row and "
                    "the rest only every 10-60 s, so rows come several times faster; cells not read are blank")
    ap.add_argument("--seconds", type=float, help="stop logging after this many seconds")
    ap.add_argument("--tag", help="word added to the log filename, e.g. baseline or after-plugs")
    ap.add_argument("--unattended", action="store_true", help="Raspberry Pi mode: wait for the engine, log each "
                    "drive to its own file, repeat forever")
    ap.add_argument("--gpio-button", type=int, metavar="PIN", help="with --unattended: GPIO pin of a marker button")
    ap.add_argument("--log-dir", help="where logs go (default logs)")
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
    set_name = "custom (--pids)" if args.pids else args.set
    unknown = [n for n in names if n not in pids.BY_NAME]
    if unknown:
        sys.exit(f"Not in the PID table: {', '.join(unknown)}. Known: {', '.join(sorted(pids.BY_NAME))}")

    if args.unattended:
        try:
            logger.unattended(opener(args), names, args.honda, args.gpio_button, args.tag or "auto",
                              directory=args.log_dir, quick=args.quick, set_name=set_name)
        except KeyboardInterrupt:
            pass
        return

    car = open_car(args)
    try:
        if args.clear_codes:
            clear_codes(car)
        elif args.report:
            health(car)
        elif args.codes:
            show(report.codes(car))
        elif args.supported:
            show(report.supported(car))
        elif args.misfires:
            show(report.misfires(car))
        elif args.monitors:
            show(report.mode06(car)[0])
        elif args.info:
            show(report.vehicle(car))
        elif args.modules:
            if args.sweep:
                print("Sweeping every address with a read-only 01 00 (about 40 s)...")
                car.discover(sweep=True)
            for a, m in car.modules.items():
                name, _ = car.identity(a)
                print(f"  {a:02X}  {m.name:20} {name or '':20} {len(m.pids)} Mode 01 PIDs")
        elif args.honda_probe:
            show(report.honda_probe(car))
        elif args.query:
            query(car, args.query, args.module)
        else:
            logger.drive(car, names, args.seconds, args.tag, args.honda, reopen=opener(args), directory=args.log_dir,
                         quick=args.quick, set_name=set_name)
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
