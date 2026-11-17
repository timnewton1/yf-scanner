"""Drive logging: CSV rows, noise markers, surviving a dropped link, and an unattended mode for a
Raspberry Pi that starts a file when the engine starts and closes it when it stops.

Read-only: only Mode 01 and the Honda Mode 22 candidates are requested.
"""

import csv
import datetime
import os
import platform
import queue
import subprocess
import sys
import threading
import time

from . import honda
from .car import ElmError
from .elm import NotConnected
from .elm.session import NoPrompt
from .obd2 import pids

LOG_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"))

SETS = {
    # Three requests a row on the Pilot: about 2 rows/s over BLE. Baseline and longevity logs.
    "drive": ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "COOLANT_TEMP", "INTAKE_TEMP",
              "INTAKE_PRESSURE", "MAF", "TIMING_ADVANCE", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
              "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "O2_S1_WR_CURRENT", "O2_S5_WR_CURRENT",
              "O2_B1S2", "O2_B2S2", "CONTROL_MODULE_VOLTAGE"],
    # One request a row (6 PIDs): what you need to tell rpm-, speed- and load-linked noises apart.
    "noise": ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "TIMING_ADVANCE", "SHORT_FUEL_TRIM_1"],
    # Everything the table knows; unsupported ones are dropped at start.
    "all": [p.name for p in pids.TABLE],
}

# Quick mode (--quick, the Quick switch): seconds between reads of each value, so each row is about
# one request plus at most one more. 0 = every row, None = once per log. Values not listed: QUICK_DEFAULT.
# Fast: the noise set (tells rpm-, speed- and load-linked noises apart) and VCM, which switches in
# about a second. About 10 s: trims, airflow, temperatures, sensors. About 60 s and once: values that
# move slowly or not at all during a drive.
QUICK_DEFAULT = 10.0
QUICK_EVERY = {
    **dict.fromkeys(SETS["noise"] + ["VCM_CYLINDERS"], 0),
    **dict.fromkeys(["CATALYST_TEMP_B1S1", "CATALYST_TEMP_B2S1", "EVAP_VAPOR_PRESSURE", "BAROMETRIC_PRESSURE",
                     "FUEL_LEVEL", "AMBIANT_AIR_TEMP", "RUN_TIME", "DISTANCE_W_MIL", "DISTANCE_SINCE_DTC_CLEAR"], 60.0),
    **dict.fromkeys(["WARMUPS_SINCE_DTC_CLEAR", "COMMANDED_EGR", "EGR_ERROR"], None),
}
QUICK_EXTRA = 6  # slower values asked per row at most (one Mode 01 request), most overdue first


def quick_every(name):
    return QUICK_EVERY.get(name, QUICK_DEFAULT)


LIVE_LINE = ["RPM", "SPEED", "ENGINE_LOAD", "COOLANT_TEMP", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
             "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "CONTROL_MODULE_VOLTAGE"]


def label(name):
    if name in honda.BY_NAME:
        return honda.BY_NAME[name]["label"]
    return pids.BY_NAME[name].label


# --- what a log was taken with ------------------------------------------------------

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def computer():
    """The OS (and phone or board) this runs on, in a few words."""
    if sys.platform == "android":
        try:
            a = platform.android_ver()
            return (f"Android {a.release} (API {a.api_level}), {a.manufacturer} {a.model}"
                    + (", emulator" if a.is_emulator else ""))
        except Exception:
            return "Android"
    system, release, machine = platform.system(), platform.release(), platform.machine()
    if system == "Linux":
        try:
            name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except OSError:
            name = "Linux"
        extra = [f"kernel {release}", machine]
        if "microsoft" in release.lower():
            extra.append("WSL")
        try:
            with open("/proc/device-tree/model", encoding="utf-8", errors="replace") as f:
                extra.append(f.read().strip("\x00\n "))  # Raspberry Pi 4 Model B Rev 1.4
        except OSError:
            pass
        return f"{name} ({', '.join(extra)})"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]} ({machine})"
    if system == "Windows":
        return f"Windows {release} ({platform.version()}, {machine})"
    return f"{system} {release} ({machine})"


def scanner_version():
    """The repo's commit (git describe), or None where there's no git (the phone)."""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", REPO, "describe", "--always", "--dirty"], capture_output=True,
                           text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


class CsvLog:
    """One drive log. Lines starting with # are run info: before the column header (what the log
    was taken with) and one at the end (how it went); summarize.py reads and skips them."""

    def __init__(self, names, tag, directory=None, info=None, demo=False):
        """info: [(key, value)] written as '# key: value' before the header. demo: the simulator,
        so the file name ends in _DEMO."""
        directory = directory or LOG_DIR
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = (f"_{tag}" if tag else "") + ("_DEMO" if demo else "")
        self.path = os.path.join(directory, f"yf_{stamp}{suffix}.csv")
        self.names = names
        self.file = open(self.path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        for k, v in info or []:
            self._comment(f"{k}: {v}")
        self.writer.writerow(["time", "elapsed_s", "marker"] + [label(n) for n in names])
        self.t0 = time.monotonic()
        self.rows = 0

    def write(self, cells, marker=""):
        now = datetime.datetime.now().isoformat(timespec="milliseconds")
        self.writer.writerow([now, f"{time.monotonic() - self.t0:.2f}", marker] +
                             ["" if cells.get(n) is None else cells[n] for n in self.names])
        self.file.flush()  # every row: a pulled plug loses one row, not the file
        self.rows += 1
        if self.rows % 10 == 0:
            os.fsync(self.file.fileno())  # a Pi losing power mid-drive keeps all but the last few seconds

    @classmethod
    def for_logger(cls, lg, tag, directory=None, front_end="scan.py", set_name=None):
        """A log for Logger lg, headed with its run info."""
        return cls(lg.columns, tag, directory, lg.run_info(front_end, set_name, tag), demo=lg.demo)

    def _comment(self, text):
        self.file.write("# " + " ".join(str(text).split()) + "\r\n")  # csv.writer's line ending

    def close(self, car=None):
        """car: also write how the log went (rows, rate, the link's retries) as the last line."""
        if car is not None and not self.file.closed:
            ended = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            self._comment(f"ended: {ended}, {stats_line(self, car)}")
        self.file.close()


# --- markers ----------------------------------------------------------------------

class Markers:
    """Lines typed on the keyboard (Enter alone = 'markN') and GPIO button presses, as marker text."""

    def __init__(self):
        self.q = queue.Queue()
        self.n = 0

    def add(self, text=None):
        self.n += 1
        self.q.put(text or f"mark{self.n}")

    def from_stdin(self):
        def run():
            for line in sys.stdin:
                self.add(line.strip() or None)
        threading.Thread(target=run, daemon=True).start()

    def from_gpio(self, pin):
        """A push button between a GPIO pin and ground (Raspberry Pi; needs gpiozero)."""
        try:
            from gpiozero import Button
        except ImportError:
            print("--gpio-button needs gpiozero (uv sync --extra pi); markers from the button are off")
            return None
        b = Button(pin, bounce_time=0.05)
        b.when_pressed = lambda: self.add("button")
        return b

    def take(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get())
        return ";".join(out)


# --- the logging loop -------------------------------------------------------------

class Logger:
    retry_delay = 3.0  # seconds between reconnect attempts

    def __init__(self, car, names, with_honda=False, reopen=None, quiet=False, quick=False, clock=time.monotonic):
        """car: an open Car. reopen: callable() -> Car, used to reconnect after a dropped link.
        quick: read each value only as often as QUICK_EVERY says; a row then has cells only for what
        it read (the rest are blank in the CSV), and `latest` holds the last value of every column."""
        self.car = car
        self.reopen = reopen
        self.quiet = quiet
        self.quick = quick
        self.clock = clock
        self.latest = {}   # column -> last value read, for screens (a quick row reads only some)
        self.due = {}      # quick: column -> when it's next read (None: never again); absent = now
        eng = car.modules.get(car.engine)
        self.skipped = [n for n in names if eng is None or pids.BY_NAME[n].pid not in eng.pids]
        self.names = [n for n in names if n not in self.skipped]
        self.extras = [c for c in honda.CANDIDATES] if with_honda and car.elm.is_29bit else []

    @property
    def columns(self):
        return self.names + [c["name"] for c in self.extras]

    @property
    def demo(self):
        ad = getattr(self.car, "adapter", None)
        return getattr(ad, "kind", "") == "simulator"

    def run_info(self, front_end, set_name=None, tag=None):
        """[(key, value)] for the top of a log: when, real car or simulator, on what, through which
        adapter, and what's logged how often. No VIN and no adapter address (logs get shared)."""
        car, ad = self.car, getattr(self.car, "adapter", None)
        ident = getattr(ad, "ident", {}) or {}
        info = [
            ("yf-scanner drive log", f"started {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"),
            ("source", "SIMULATOR (DEMO), not a car" if self.demo else "real car"),
            ("front end", front_end),
            ("computer", computer()),
            ("python", platform.python_version()),
        ]
        v = scanner_version()
        if v:
            info.append(("scanner", f"git {v}"))
        if ad is not None:
            info.append(("adapter", f"{ident.get('chip') or '?'} over {ad.kind}, type: {ident.get('type', '?')}"))
            info += [("adapter note", n) for n in ident.get("notes", [])]
        info.append(("car", f"{car.elm.protocol_name}; modules "
                     + ", ".join(f"{a:02X} {m.name} ({len(m.pids)} PIDs)" for a, m in car.modules.items())))
        m = car.modules.get(car.engine)
        if m is not None and not m.batch:
            info.append(("engine computer", "one PID per request (it missed multi-PID requests)"))
        info.append(("set", f"{set_name or 'custom'} ({len(self.columns)} values), Honda reads "
                     f"{'on' if self.extras else 'off'}, quick {'on' if self.quick else 'off'}"
                     + (f", tag {tag}" if tag else "")))
        if self.quick:
            groups = {}
            for n in self.columns:
                groups.setdefault(quick_every(n), []).append(label(n))
            for every in sorted(groups, key=lambda e: float("inf") if e is None else e):
                when = "every row" if every == 0 else "once" if every is None else f"every {every:.0f} s"
                info.append((f"quick, {when}", ", ".join(groups[every])))
        if self.skipped:
            info.append(("not answered, skipped", ", ".join(self.skipped)))
        return info

    def row(self):
        """Read one row -> {column: value}. Quick mode returns only the columns it read, which can be
        none when the set has no fast values and nothing slower is due yet."""
        names, extras = self.names, self.extras
        if self.quick:
            names, extras = self._due_now()
        cells = self.car.read(names) if names else {}
        for c in extras:
            cells[c["name"]] = self.car.honda(c)[0]
        if self.quick:
            now = self.clock()
            for n, v in cells.items():
                every = quick_every(n)
                if every:
                    self.due[n] = now + every
                elif every is None:  # once: again later only if it didn't answer
                    self.due[n] = None if v is not None else now + QUICK_DEFAULT
        self.latest.update(cells)
        return cells

    def _due_now(self):
        """Quick mode: the Mode 01 names and Honda candidates to read this row."""
        now = self.clock()
        due = lambda n: n not in self.due or (self.due[n] is not None and self.due[n] <= now)
        fast = [n for n in self.names if quick_every(n) == 0]
        slow = sorted((n for n in self.names if quick_every(n) != 0 and due(n)), key=lambda n: self.due.get(n, 0))
        extras = [c for c in self.extras if quick_every(c["name"]) == 0 or due(c["name"])]
        return fast + slow[:QUICK_EXTRA], extras

    def idle(self):
        """Quick mode, nothing to read this row: wait (at most 0.25 s, so Stop stays quick) for the
        next value due."""
        nxt = [t for t in self.due.values() if t is not None]
        time.sleep(max(0.0, min(0.25, min(nxt) - self.clock())) if nxt else 0.25)

    def reconnect(self, give_up_after=None):
        """Keep trying to reopen the car. Returns True when connected, False if give_up_after
        seconds pass first."""
        try:
            self.car.close()
        except Exception:
            pass
        start = time.monotonic()
        while give_up_after is None or time.monotonic() - start < give_up_after:
            time.sleep(self.retry_delay)
            try:
                car = self.reopen()
            except (NotConnected, ElmError, OSError):
                continue
            if car.connected:
                self.car = car
                return True
            car.close()
        return False

    def run(self, log, markers, seconds=None, stop_when=None, link_patience=None):
        """Write rows until Ctrl+C, `seconds`, or stop_when(latest) returns True (latest: every column's
        last value, which in quick mode is more than the row just read).
        A dropped link is retried (forever, or for link_patience seconds) and marked in the log."""
        stop_at = time.monotonic() + seconds if seconds else None
        tty = sys.stdout.isatty() and not self.quiet
        slow = 0
        while stop_at is None or time.monotonic() < stop_at:
            err = None
            try:
                cells = self.row()
                slow = 0
                if not cells:  # quick, nothing due yet
                    if stop_when and stop_when(dict(self.latest)):
                        return "stopped"
                    self.idle()
                    continue
            except NoPrompt as e:
                slow += 1
                if slow < 3:  # one slow reply isn't a lost link; three in a row is
                    log.write({}, "timeout")
                    continue
                err = f"{slow} timeouts in a row: {e}"
                slow = 0
            except ElmError as e:
                err = str(e)
            if err is not None:
                log.write({}, f"link lost: {err[:60]}")
                if not self.quiet:
                    print(f"\nLink lost ({err}). Reconnecting... (Ctrl+C to stop)", flush=True)
                if self.reopen is None or not self.reconnect(link_patience):
                    return "link"
                log.write({}, "reconnected")
                if not self.quiet:
                    print("Reconnected.", flush=True)
                continue
            marker = markers.take() if markers else ""
            log.write(cells, marker)
            if marker and not self.quiet:
                print(f"\n  marker '{marker}' at {time.monotonic() - log.t0:.1f} s")
            if not self.quiet:
                shown = self.latest
                line = "  ".join(f"{label(n)} {'' if shown.get(n) is None else shown[n]}" for n in LIVE_LINE if n in shown)
                print(("\r" if tty else "") + line + "   ", end="" if tty else "\n", flush=True)
            if stop_when and stop_when(dict(self.latest)):  # what screens show: every column's last value
                return "stopped"
        return "time"


def drive(car, names, seconds=None, tag=None, with_honda=False, reopen=None, directory=None, quick=False,
          set_name=None):
    """Interactive drive log (the default command)."""
    lg = Logger(car, names, with_honda, reopen, quick=quick)
    if not lg.names:
        sys.exit("None of the requested PIDs are supported by the engine module.")
    if lg.skipped:
        print(f"Not answered by this engine computer, skipped: {', '.join(lg.skipped)}")
    log = CsvLog.for_logger(lg, tag, directory, "scan.py", set_name)
    markers = Markers()
    if sys.stdin.isatty():
        markers.from_stdin()
        print("Press Enter to drop a marker when you hear something; type a word first (e.g. 'whine') to label it.")
    how = " (quick: the noise values every row, the rest every 10-60 s)" if quick else ""
    print(f"Logging {len(lg.columns)} values{how} to {log.path}  (Ctrl+C to stop)", flush=True)
    try:
        lg.run(log, markers, seconds)
    except KeyboardInterrupt:
        pass
    finally:
        log.close(lg.car)
        print("\n" + saved_line(log, lg.car))
        lg.car.close()


def stats_line(log, car):
    """How a log went: rows, rate, and the link's retries and timeouts."""
    secs = time.monotonic() - log.t0
    st = car.elm.stats
    return (f"{log.rows} rows in {secs:.0f} s ({log.rows / max(secs, 1e-3):.2f} rows/s); "
            f"link: {st['requests']} requests, {st['retries']} retried for missing frames, {st['timeouts']} timeouts")


def saved_line(log, car):
    return f"Saved {log.path}: {stats_line(log, car)}"


def unattended(open_car, names, with_honda=False, gpio_button=None, tag="auto", idle_stop=20.0,
               directory=None, quick=False, set_name=None):
    """For a Raspberry Pi in the car: wait for the adapter and a running engine, log the drive to
    its own file, close it when the engine has been off for idle_stop seconds, repeat. Never exits
    on its own; stop it with Ctrl+C or systemctl stop."""
    markers = Markers()
    if gpio_button is not None:
        markers._button = markers.from_gpio(gpio_button)
    say = lambda s: print(f"{datetime.datetime.now():%H:%M:%S} {s}", flush=True)
    say("unattended logger started")
    while True:
        try:
            car = open_car()
        except (NotConnected, ElmError, OSError) as e:
            say(f"no adapter ({str(e)[:80]}); retrying in 15 s")
            time.sleep(15)
            continue
        if not car.connected:
            car.close()
            say("adapter found, car not answering (key off?); retrying in 15 s")
            time.sleep(15)
            continue
        try:
            rpm = car.read(["RPM"])["RPM"]
        except ElmError:
            car.close()
            continue
        if not rpm or rpm < 300:
            car.close()
            time.sleep(10)
            continue
        lg = Logger(car, names, with_honda, reopen=open_car, quiet=True, quick=quick)
        log = CsvLog.for_logger(lg, tag, directory, "scan.py --unattended", set_name)
        say(f"engine running ({rpm:.0f} rpm): logging to {log.path}")
        off_since = [None]

        def engine_off(cells):
            r = cells.get("RPM")
            if r and r > 300:
                off_since[0] = None
                return False
            off_since[0] = off_since[0] or time.monotonic()
            return time.monotonic() - off_since[0] > idle_stop

        markers.take()  # drop presses from before the drive
        why = lg.run(log, markers, stop_when=engine_off, link_patience=60)
        log.close(lg.car)
        say(f"closed {log.path} ({log.rows} rows, {why})")
        try:
            lg.car.close()
        except Exception:
            pass
        time.sleep(5)
