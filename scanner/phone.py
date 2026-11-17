"""The phone app's way into the scanner (android/). The app embeds Python (Chaquopy) and runs this,
so the phone reads the car with the same Car, report, logger and summarize code as scan.py and
tui.py. Kotlin keeps only what needs Android: the BLE link (BleLink.kt), the screens, and the
service that keeps a drive log going with the screen off.

Kotlin calls these from one worker thread, so one request is on the adapter at a time (tui.py's
lock). Calls take plain values and return JSON text.

hooks, passed to connect(): a Kotlin object (PyHooks in Py.kt) with
    openLink(address) -> Link   an open BleLink: write(bytes), read(timeout_ms) -> bytes, resetInput(), close(), info()
    traffic(direction, text)    every line to and from the adapter, for the Console
    section(json)               a report section, as soon as it's read
    row(json)                   a drive-log row: {"cells": {name: text}, "marker", "rows", "seconds"}
    status(text)                the link dropped / came back while logging
    stopRequested() -> bool     the Stop button
    takeMarker() -> str         MARK presses since the last row, ";"-joined ("" for none)
"""

import json
import os
import sys
import time

from . import elm, logger, report
from .car import Car, ElmError
from .elm import NotConnected, ble
from .report import Section

DEMO = "sim://pilot"

_hooks = None
_car = None
_target = None
_log = None  # (Logger, CsvLog) from log_prepare() until log_run() ends


def _msg(e):
    """A Java exception's own message rather than its class name."""
    get = getattr(e, "getMessage", None)
    return str(get() if callable(get) else e)


def _json(**kw):
    return json.dumps(kw)


class LinkPort:
    """The pyserial-style port session.py reads and writes, over a Kotlin Link."""

    timeout = 0.05  # seconds a read waits for the first byte, like the serial ports (adapter.open_port)

    def __init__(self, link):
        self.link = link
        self.buf = bytearray()

    @property
    def in_waiting(self):
        return len(self.buf)

    def read(self, size=1):
        if not self.buf:
            try:
                self.buf.extend(bytes(self.link.read(int(self.timeout * 1000))))
            except Exception as e:
                raise OSError(_msg(e)) from None
        out = bytes(self.buf[:size])
        del self.buf[:size]
        return out

    def write(self, data):
        try:
            self.link.write(bytes(data))
        except Exception as e:
            raise OSError(_msg(e)) from None
        return len(data)

    def reset_input_buffer(self):
        self.buf.clear()
        self.link.resetInput()

    def close(self):
        try:
            self.link.close()
        except Exception:
            pass

    def info(self):
        lines = self.link.info()  # a Kotlin List arrives as a java.util.ArrayList, which Python can't iterate
        if hasattr(lines, "toArray"):
            lines = lines.toArray()
        return [str(x) for x in lines]


class Stopped(Exception):
    """Stop pressed while the logger was reconnecting."""


# --- startup and connecting -------------------------------------------------------

def hello():
    """What the app needs before connecting: the log sets, and how to recognize a BLE OBD adapter
    (the same lists ble.py uses on the laptop)."""
    return _json(python=sys.version.split()[0], sets={k: len(v) for k, v in logger.SETS.items()},
                 profiles=[list(p) for p in ble.PROFILES], skip=sorted(ble.SKIP_SERVICES),
                 likely_name=ble.LIKELY_NAME.pattern)


def _traffic(direction, text):
    if _hooks is not None:
        _hooks.traffic(direction, text)


def _open(target):
    if target == "DEMO":
        return Car.open(DEMO, log=_traffic)

    def open_port():
        try:
            return LinkPort(_hooks.openLink(target))
        except Exception as e:
            raise NotConnected(_msg(e)) from None
    return Car.start(elm.connect_port("ble://" + target, open_port, log=_traffic))


def connect(target, hooks):
    """target: a BLE address, or "DEMO" for the simulated Pilot."""
    global _hooks, _car, _target
    _hooks = hooks
    disconnect()
    try:
        car = _open(target)
        conn = report.connection(car)
    except (NotConnected, ElmError) as e:
        return _json(ok=False, message=f"No adapter: {e}")
    _car, _target = car, target
    return _json(ok=True, car=car.connected, target=target, connection=conn.as_dict(),
                 adapter=report.adapter(car.adapter).as_dict())


def disconnect():
    global _car
    if _car is not None:
        try:
            _car.close()
        except Exception:
            pass
    _car = None


# --- reads ------------------------------------------------------------------------

READS = {
    "codes": report.codes,
    "misfires": report.misfires,
    "mode06": lambda car: report.mode06(car)[0],
    "mode06_failed": lambda car: report.mode06(car, failed_only=True)[0],
    "live": report.live,
    "vehicle": report.vehicle,
    "supported": report.supported,
    "honda_probe": report.honda_probe,
    "clear": report.clear_codes,  # the app asks first (type 'clear'), as scan.py does
}


def run(what, *args):
    """One read ("health", a READS name, or "query" with request hex and module). Each section goes
    to hooks.section() as it's read; all of them are returned too."""
    out = []

    def emit(s):
        out.append(s)
        _hooks.section(json.dumps(s.as_dict()))

    if _car is None or not _car.connected:
        emit(Section("Not connected").add("Connect to the car first (Connect tab).", "warn"))
    else:
        try:
            if what == "health":
                report.health(_car, emit)
            elif what == "query":
                emit(report.query(_car, args[0], args[1]))
            else:
                emit(READS[what](_car))
        except ValueError as e:
            emit(Section("Refused").add(str(e), "bad"))
        except ElmError as e:
            emit(Section("Lost the adapter").add(str(e), "bad").add("Reconnect on the Connect tab.", "dim"))
    return json.dumps([s.as_dict() for s in out])


# --- drive log --------------------------------------------------------------------

class _AppLog(logger.CsvLog):
    """The CSV scan.py writes, with each row also sent to the screen."""

    def write(self, cells, marker=""):
        super().write(cells, marker)
        if marker.startswith("link lost"):
            _hooks.status(f"Link lost ({marker[len('link lost: '):]}). Reconnecting...")
        elif marker == "reconnected":
            _hooks.status("Reconnected.")
        elif marker != "timeout":
            _hooks.row(_json(cells={n: report.fmt(v) for n, v in cells.items()}, marker=marker, rows=self.rows,
                             seconds=round(time.monotonic() - self.t0, 1)))


class _Markers:
    def take(self):
        return str(_hooks.takeMarker() or "")


def _reopen():
    if _hooks.stopRequested():
        raise Stopped()
    return _open(_target)


def log_prepare(set_name, with_honda, tag, directory):
    """Pick the columns and open the CSV. Returns its path, the columns, what the engine computer
    doesn't answer, and the values worth a big tile ([name, label])."""
    global _log
    if _car is None or not _car.connected:
        return _json(error="Connect to the car first")
    lg = logger.Logger(_car, logger.SETS[set_name], bool(with_honda), reopen=_reopen, quiet=True)
    if not lg.names:
        return _json(error="None of those PIDs are answered by the engine computer")
    csv = _AppLog(lg.columns, tag.strip() or None, directory)
    _log = (lg, csv)
    tiles = [n for n in logger.LIVE_LINE if n in lg.columns] + [c["name"] for c in lg.extras]
    return _json(path=csv.path, columns=lg.columns, skipped=lg.skipped, tiles=[[n, logger.label(n)] for n in tiles])


def log_run():
    """Log until Stop (hooks.stopRequested). Blocks; reconnects after a dropped link like scan.py."""
    global _car, _log
    lg, csv = _log
    why = ""
    try:
        why = lg.run(csv, _Markers(), stop_when=lambda cells: bool(_hooks.stopRequested()))
    except Stopped:
        why = "stopped"
    except Exception as e:
        why = f"error: {_msg(e)}"
    finally:
        csv.close()
        _log = None
    _car = lg.car  # it may have reconnected to a new session
    text = logger.saved_line(csv, lg.car).replace(csv.path, os.path.basename(csv.path))
    if why == "link":
        text += "\nThe link dropped and didn't come back."
    elif why.startswith("error"):
        text += f"\n{why}"
    return _json(status=text, why=why, connected=_car.connected)


# --- logs -------------------------------------------------------------------------

def summary(path):
    """summarize.py's text for one log."""
    import summarize
    try:
        return summarize.summarize(path, name=os.path.basename(path))
    except (OSError, ValueError) as e:
        return f"Couldn't summarize: {e}"
