"""Drive logging: CSV rows, noise markers, surviving a dropped link, and an unattended mode for a
Raspberry Pi that starts a file when the engine starts and closes it when it stops.

Read-only: only Mode 01 and the Honda Mode 22 candidates are requested.
"""

import csv
import datetime
import os
import queue
import sys
import threading
import time

import honda
from car import ElmError
from elm import NotConnected
from elm.session import NoPrompt
from obd2 import pids

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

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

LIVE_LINE = ["RPM", "SPEED", "ENGINE_LOAD", "COOLANT_TEMP", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
             "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "CONTROL_MODULE_VOLTAGE"]


def label(name):
    if name in honda.BY_NAME:
        return honda.BY_NAME[name]["label"]
    return pids.BY_NAME[name].label


class CsvLog:
    def __init__(self, names, tag, directory=None):
        directory = directory or LOG_DIR
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(directory, f"yf_{stamp}{'_' + tag if tag else ''}.csv")
        self.names = names
        self.file = open(self.path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(["time", "elapsed_s", "marker"] + [label(n) for n in names])
        self.t0 = time.monotonic()
        self.rows = 0

    def write(self, cells, marker=""):
        now = datetime.datetime.now().isoformat(timespec="milliseconds")
        self.writer.writerow([now, f"{time.monotonic() - self.t0:.2f}", marker] +
                             ["" if cells.get(n) is None else cells[n] for n in self.names])
        self.file.flush()  # every row: a pulled plug loses one row, not the file
        self.rows += 1
        if self.rows % 20 == 0:
            os.fsync(self.file.fileno())  # a Pi losing power mid-drive keeps all but the last few seconds

    def close(self):
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
            print("--gpio-button needs gpiozero (pip install gpiozero); markers from the button are off")
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
    def __init__(self, car, names, with_honda=False, reopen=None, quiet=False):
        """car: an open Car. reopen: callable() -> Car, used to reconnect after a dropped link."""
        self.car = car
        self.reopen = reopen
        self.quiet = quiet
        eng = car.modules.get(car.engine)
        self.skipped = [n for n in names if eng is None or pids.BY_NAME[n].pid not in eng.pids]
        self.names = [n for n in names if n not in self.skipped]
        self.extras = [c for c in honda.CANDIDATES] if with_honda and car.elm.is_29bit else []

    @property
    def columns(self):
        return self.names + [c["name"] for c in self.extras]

    def row(self):
        cells = self.car.read(self.names)
        for c in self.extras:
            cells[c["name"]] = self.car.honda(c)[0]
        return cells

    def reconnect(self, give_up_after=None):
        """Keep trying to reopen the car. Returns True when connected, False if give_up_after
        seconds pass first."""
        try:
            self.car.close()
        except Exception:
            pass
        start = time.monotonic()
        while give_up_after is None or time.monotonic() - start < give_up_after:
            time.sleep(3)
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
        """Write rows until Ctrl+C, `seconds`, or stop_when(cells) returns True.
        A dropped link is retried (forever, or for link_patience seconds) and marked in the log."""
        stop_at = time.monotonic() + seconds if seconds else None
        tty = sys.stdout.isatty() and not self.quiet
        slow = 0
        while stop_at is None or time.monotonic() < stop_at:
            err = None
            try:
                cells = self.row()
                slow = 0
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
                line = "  ".join(f"{label(n)} {'' if cells.get(n) is None else cells[n]}" for n in LIVE_LINE if n in cells)
                print(("\r" if tty else "") + line + "   ", end="" if tty else "\n", flush=True)
            if stop_when and stop_when(cells):
                return "stopped"
        return "time"


def drive(car, names, seconds=None, tag=None, with_honda=False, reopen=None, directory=None):
    """Interactive drive log (the default command)."""
    lg = Logger(car, names, with_honda, reopen)
    if not lg.names:
        sys.exit("None of the requested PIDs are supported by the engine module.")
    if lg.skipped:
        print(f"Not answered by this engine computer, skipped: {', '.join(lg.skipped)}")
    log = CsvLog(lg.columns, tag, directory)
    markers = Markers()
    if sys.stdin.isatty():
        markers.from_stdin()
        print("Press Enter to drop a marker when you hear something; type a word first (e.g. 'whine') to label it.")
    print(f"Logging {len(lg.columns)} values to {log.path}  (Ctrl+C to stop)", flush=True)
    try:
        lg.run(log, markers, seconds)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        secs = time.monotonic() - log.t0
        st = lg.car.elm.stats
        print(f"\nSaved {log.path}: {log.rows} rows in {secs:.0f} s ({log.rows / max(secs, 1e-3):.2f} rows/s); "
              f"link: {st['requests']} requests, {st['retries']} retried for missing frames, {st['timeouts']} timeouts")
        lg.car.close()


def unattended(open_car, names, with_honda=False, gpio_button=None, tag="auto", idle_stop=20.0,
               directory=None):
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
        lg = Logger(car, names, with_honda, reopen=open_car, quiet=True)
        log = CsvLog(lg.columns, tag, directory)
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
        log.close()
        say(f"closed {log.path} ({log.rows} rows, {why})")
        try:
            lg.car.close()
        except Exception:
            pass
        time.sleep(5)
