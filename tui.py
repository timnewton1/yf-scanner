"""YF TUI: the whole scanner in one terminal screen, from finding the adapter to summarizing a drive log.

    .venv/bin/python tui.py --port DEMO          simulated Pilot (prove the laptop side first)
    .venv/bin/python tui.py --connect            the real car, key ON: the saved adapter (.adapter), once
                                                 one has connected; before that, --port auto finds it

Tabs (keys 1-7):
    1 Connect   find adapters, connect, what the adapter says it is, which modules answer
    2 Health    codes in every module, readiness, misfires, failed Mode 06 tests, Honda reads;
                clearing codes is here too and asks first (it resets readiness and fuel trims)
    3 Live      live values with sparklines; start a CSV log; m or the marker box drops a noise marker
    4 Mode 06   every Mode 06 test result with its limits
    5 Info      VIN, module names, calibration IDs, supported PIDs
    6 Console   one read-only request (services 01 02 03 06 07 09 0A 22) to one module; raw adapter traffic
    7 Logs      the CSVs in logs/ and their summaries

Read-only except "Clear codes", same as scan.py. Needs textual (pip install -r requirements.txt).
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from collections import deque

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, RichLog, Select, Sparkline, Static,
                             Switch, TabbedContent, TabPane)

from scanner import elm, honda, logger
from scanner.car import READ_ONLY_SERVICES, Car
from scanner.elm import NotConnected
from scanner.elm.session import nrc_text
from scanner.obd2 import dtc, monitors, pids
from scan import REPORT_LIVE, fmt

SPARKS = [("RPM", "rpm"), ("SPEED", "mph"), ("COOLANT_TEMP", "coolant F"), ("ENGINE_LOAD", "load %")]
HISTORY = 120  # rows of sparkline history


def err_text(e):
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


# --- logging plumbing -------------------------------------------------------------

class SwitchableLog:
    """What Logger.run writes to: a CSV while recording, nothing otherwise. Link events are passed on
    to the screen either way."""

    def __init__(self, on_event):
        self.csv = None
        self.on_event = on_event
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.rows = 0

    def start(self, columns, tag, directory):
        with self.lock:
            self.csv = logger.CsvLog(columns, tag, directory)
            return self.csv.path

    def stop(self):
        with self.lock:
            c, self.csv = self.csv, None
            if c:
                c.close()
            return c

    def write(self, cells, marker=""):
        self.rows += 1
        if marker.startswith(("timeout", "link lost", "reconnected")):
            self.on_event(marker)
        with self.lock:
            if self.csv:
                self.csv.write(cells, marker)


class LockedLogger(logger.Logger):
    """logger.Logger whose reads share the app's car lock, so a Health refresh can run mid-log."""

    def __init__(self, app, *a, **kw):
        super().__init__(*a, **kw)
        self.app_ = app

    def row(self):
        with self.app_.lock:
            return super().row()

    def reconnect(self, give_up_after=None):
        self.app_.status("Link lost; reconnecting...", "warning")
        ok = super().reconnect(give_up_after)
        if ok:
            self.app_.adopt(self.car)
        return ok


# --- dialogs ----------------------------------------------------------------------

class ClearCodes(ModalScreen[bool]):
    """Type 'clear' to go ahead, same as scan.py --clear-codes."""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                "[b]Clear codes?[/b]\n\n"
                "This also resets [b]every readiness monitor[/b] and the [b]learned fuel trims[/b].\n"
                "The car will [b red]FAIL an emissions test[/b red] until the monitors complete again "
                "(days of driving).\nPermanent codes stay until the car confirms the fix itself.\n"
                "Key ON, engine OFF.\n\nType [b]clear[/b] and press Enter to go ahead; Esc cancels.")
            yield Input(placeholder="clear", id="confirm")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel", variant="primary")

    @on(Input.Submitted, "#confirm")
    def submitted(self, ev):
        self.dismiss(ev.value.strip().lower() == "clear")

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(False)

    def key_escape(self):
        self.dismiss(False)


# --- the app ----------------------------------------------------------------------

class YfTui(App):
    TITLE = "YF Scan"
    SUB_TITLE = "2009-2015 Honda Pilot"
    CSS = """
    Screen { layout: vertical; }
    #statusbar { height: 1; padding: 0 1; background: $panel; }
    #statusbar.error { background: $error 40%; }
    #statusbar.warning { background: $warning 40%; }
    .row { height: auto; margin: 0 0 1 0; }
    .row > * { margin-right: 1; }
    .row Label { padding: 1 0 0 0; }
    #port { width: 36; }
    #timeout { width: 10; }
    #tag, #marker { width: 24; }
    #query { width: 20; }
    #module, #pidset { width: 26; }
    #adapters, #modules { height: auto; max-height: 12; margin-bottom: 1; }
    #livetable { width: 60; }
    #sparks { width: 1fr; padding-left: 2; }
    #sparks Sparkline { height: 3; margin-bottom: 1; }
    #sparks Label { height: 1; }
    #loglist { width: 52; }
    #summary { width: 1fr; }
    #rawlog { height: 1fr; border: round $panel; }
    #queryout { height: auto; max-height: 14; border: round $panel; }
    ClearCodes { align: center middle; }
    #dialog { width: 72; height: auto; padding: 1 2; border: thick $error; background: $surface; }
    #dialog Input { margin-top: 1; }
    #dialog .buttons { height: auto; margin-top: 1; }
    """
    BINDINGS = [
        Binding("1", "tab('connect')", "Connect"), Binding("2", "tab('health')", "Health"),
        Binding("3", "tab('live')", "Live"), Binding("4", "tab('mode06')", "Mode 06"),
        Binding("5", "tab('info')", "Info"), Binding("6", "tab('console')", "Console"),
        Binding("7", "tab('logs')", "Logs"),
        Binding("m", "marker", "Marker"), Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, port="DEMO", timeout=1.0, protocol=None, baud=None, log_dir=None, connect=False):
        super().__init__()
        self.connect_on_start = connect
        self.port, self.timeout, self.protocol, self.baud = port, timeout, protocol, baud
        self.log_dir = log_dir or logger.LOG_DIR
        self.car = None
        self.lock = threading.RLock()   # one request on the adapter at a time
        self.raw_on = False
        self.live_stop = threading.Event()
        self.live_running = False
        self.markers = logger.Markers()
        self.sink = SwitchableLog(lambda m: self.status(f"link: {m}", "warning"))
        self.lg = None
        self.history = {n: deque(maxlen=HISTORY) for n, _ in SPARKS}
        self.rate = deque(maxlen=20)

    # --- layout ---------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="connect"):
            with TabPane("1 Connect", id="connect"):
                with VerticalScroll():
                    with Horizontal(classes="row"):
                        yield Label("Port")
                        yield Input(self.port, id="port", placeholder="auto, DEMO, ble:<address>, /dev/ttyUSB0")
                        yield Label("Timeout s")
                        yield Input(str(self.timeout), id="timeout")
                        yield Button("Connect", id="connect-btn", variant="success")
                        yield Button("Disconnect", id="disconnect-btn")
                        yield Button("Find adapters", id="scan-btn")
                    yield Static("Not connected. DEMO is a simulated 2011 Pilot; try it first.", id="adapterinfo")
                    yield Label("Adapters found (select one to use it):")
                    yield DataTable(id="adapters", cursor_type="row")
                    yield Label("Modules that answer OBD requests:")
                    yield DataTable(id="modules", cursor_type="row")
            with TabPane("2 Health", id="health"):
                with Horizontal(classes="row"):
                    yield Button("Refresh (r)", id="health-btn", variant="primary")
                    yield Button("Clear codes...", id="clear-btn", variant="error")
                with VerticalScroll():
                    yield Static("Connect first, then Refresh.", id="healthout")
            with TabPane("3 Live", id="live"):
                with Horizontal(classes="row"):
                    yield Label("Set")
                    yield Select([(f"{k} ({len(v)} PIDs)", k) for k, v in logger.SETS.items()],
                                 value="drive", allow_blank=False, id="pidset")
                    yield Label("Honda")
                    yield Switch(value=False, id="honda")
                    yield Button("Start live", id="live-btn", variant="primary")
                with Horizontal(classes="row"):
                    yield Label("Tag")
                    yield Input(placeholder="baseline", id="tag")
                    yield Button("Record CSV", id="rec-btn", variant="success", disabled=True)
                    yield Label("Marker")
                    yield Input(placeholder="whine / drone / clunk", id="marker", disabled=True)
                yield Static("", id="livestat")
                with Horizontal():
                    yield DataTable(id="livetable", cursor_type="none")
                    with Vertical(id="sparks"):
                        for n, lab in SPARKS:
                            yield Label(lab, id=f"sl-{n}")
                            yield Sparkline([], id=f"sp-{n}")
            with TabPane("4 Mode 06", id="mode06"):
                with Horizontal(classes="row"):
                    yield Button("Read all tests (r)", id="mode06-btn", variant="primary")
                    yield Static("", id="mode06stat")
                yield DataTable(id="mode06table", cursor_type="row", zebra_stripes=True)
            with TabPane("5 Info", id="info"):
                with Horizontal(classes="row"):
                    yield Button("Read (r)", id="info-btn", variant="primary")
                with VerticalScroll():
                    yield Static("", id="infoout")
            with TabPane("6 Console", id="console"):
                with Horizontal(classes="row"):
                    yield Label("Request")
                    yield Input(placeholder="0902", id="query")
                    yield Label("to")
                    yield Select([("engine (ECM)", "engine"), ("transmission (TCM)", "transmission")],
                                 value="engine", allow_blank=False, id="module")
                    yield Button("Send", id="query-btn", variant="primary")
                    yield Label("Raw traffic")
                    yield Switch(value=False, id="raw")
                yield Static(f"Read-only services only: "
                             f"{', '.join(f'{s:02X}' for s in sorted(READ_ONLY_SERVICES))}. Address as a hex byte "
                             "also works in the request box after a space, e.g. '222201 1D'.")
                yield RichLog(id="queryout", markup=False, wrap=True)
                yield RichLog(id="rawlog", markup=False, max_lines=5000)
            with TabPane("7 Logs", id="logs"):
                with Horizontal(classes="row"):
                    yield Button("Rescan (r)", id="logs-btn", variant="primary")
                    yield Static(f"in {self.log_dir}", id="logdir")
                with Horizontal():
                    yield DataTable(id="loglist", cursor_type="row")
                    with VerticalScroll(id="summary"):
                        yield Static("Select a log to summarize it.", id="summaryout")
        yield Static("", id="statusbar")
        yield Footer()

    def on_mount(self):
        self.query_one("#adapters", DataTable).add_columns("port", "kind", "description")
        self.query_one("#modules", DataTable).add_columns("addr", "module", "Mode 01 PIDs")
        self.query_one("#mode06table", DataTable).add_columns("monitor", "test", "value", "unit", "min", "max", "result")
        self.query_one("#loglist", DataTable).add_columns("file", "KB", "modified")
        lt = self.query_one("#livetable", DataTable)
        lt.add_column("value", key="name")
        lt.add_column("now", key="now", width=12)
        lt.add_column("min", key="min", width=9)
        lt.add_column("max", key="max", width=9)
        self.scan_logs()
        self.status("Press Connect (DEMO needs no hardware). Keys 1-7 switch tabs, q quits.")
        if self.connect_on_start:
            self.do_connect()

    # --- small helpers --------------------------------------------------------------

    def status(self, msg, level=""):
        """Safe from any thread."""
        def set_():
            bar = self.query_one("#statusbar", Static)
            bar.update(msg)
            bar.set_classes(level)
        if threading.current_thread() is threading.main_thread():
            set_()
        else:
            self.call_from_thread(set_)

    def ui(self, fn, *a):
        self.call_from_thread(fn, *a)

    def need_car(self):
        if self.car is None or not self.car.connected:
            self.status("Not connected to the car. Tab 1: Connect.", "error")
            return False
        return True

    def raw_traffic(self, direction, text):
        if self.raw_on:
            self.call_from_thread(self.query_one("#rawlog", RichLog).write, f"{direction} {text}")

    def open_car(self):
        return Car.open(self.port, timeout=self.timeout, protocol=self.protocol, baud=self.baud,
                        log=self.raw_traffic)

    def reopen(self):
        with self.lock:
            return self.open_car()

    def adopt(self, car):
        self.car = car
        self.ui(self.show_connected)

    def action_tab(self, tab):
        # Drop focus first: a focused widget in the pane being hidden hands focus to the next one,
        # which pulls TabbedContent back to that widget's pane.
        self.set_focus(None)
        self.query_one(TabbedContent).active = tab

    def action_refresh(self):
        tab = self.query_one(TabbedContent).active
        {"health": self.do_health, "mode06": self.do_mode06, "info": self.do_info,
         "logs": self.scan_logs}.get(tab, lambda: None)()

    # --- 1 connect ------------------------------------------------------------------

    @on(Button.Pressed, "#connect-btn")
    def press_connect(self):
        self.port = self.query_one("#port", Input).value.strip() or "auto"
        try:
            self.timeout = float(self.query_one("#timeout", Input).value)
        except ValueError:
            self.status("Timeout must be a number of seconds.", "error")
            return
        self.stop_live()
        self.do_connect()

    @work(thread=True, exclusive=True, group="connect")
    def do_connect(self):
        self.status(f"Connecting on {self.port}... (a BLE scan takes about 6 s)")
        with self.lock:
            if self.car:
                try:
                    self.car.close()
                except Exception:
                    pass
                self.car = None
            try:
                car = self.open_car()
            except NotConnected as e:
                self.status(f"No adapter: {e}", "error")
                self.ui(self.query_one("#adapterinfo", Static).update, f"No adapter: {e}")
                return
            except Exception as e:
                self.status(f"Connect failed: {err_text(e)}", "error")
                return
            self.car = car
            self.volts = car.elm.voltage() if car.connected else None
        self.ui(self.show_connected)

    def show_connected(self):
        car = self.car
        ad = car.adapter
        i = ad.ident
        t = Text()
        t.append("Adapter: ", style="bold")
        t.append(f"{i.get('chip') or '?'} over {ad.kind} ({ad.url})\n")
        typ = i.get("type", "?")
        t.append("type: ", style="bold")
        t.append(typ + "\n", style="green" if typ == elm.REFERENCE["name"] else "yellow")
        for key, cmd in elm.adapter.IDENT:
            t.append(f"  {cmd:5} {i.get(key) or '(no answer)'}\n", style="dim")
        if ad.link is not None:
            for line in ad.link.info():
                t.append(f"  {line}\n", style="dim")
        for n in i.get("notes", []):
            t.append(f"  note: {n}\n", style="italic")
        if car.connected:
            v = getattr(self, "volts", None)
            t.append("Car: ", style="bold")
            t.append(f"{car.elm.protocol_name}{f', {v:.1f} V at the port' if v else ''}", style="green")
            self.status(f"Connected: {len(car.modules)} module(s) answering. Tab 2 for a health check.")
        else:
            t.append("Car: not answering. ", style="bold red")
            t.append("Key ON (or engine running)? Adapter pushed fully in?"
                     + (f" Protocol {self.protocol} forced: try without it." if self.protocol else ""))
            self.status("Adapter answers, the car doesn't.", "warning")
        self.query_one("#adapterinfo", Static).update(t)
        mt = self.query_one("#modules", DataTable)
        mt.clear()
        for a, m in car.modules.items():
            mt.add_row(f"{a:02X}", m.name, str(len(m.pids)))
        self.sub_title = f"{ad.kind} {ad.url}" + (" - car answering" if car.connected else " - car silent")

    @on(Button.Pressed, "#disconnect-btn")
    def press_disconnect(self):
        self.stop_live()
        self.do_disconnect()

    @work(thread=True, exclusive=True, group="connect")
    def do_disconnect(self):
        with self.lock:
            if self.car:
                try:
                    self.car.close()
                except Exception:
                    pass
            self.car = None
        self.ui(self.query_one("#adapterinfo", Static).update, "Disconnected.")
        self.ui(self.query_one("#modules", DataTable).clear)
        self.status("Disconnected.")

    @on(Button.Pressed, "#scan-btn")
    def press_scan(self):
        self.do_scan()

    @work(thread=True, exclusive=True, group="scan")
    def do_scan(self):
        self.status("Looking for adapters (USB and Bluetooth ports, then a 6 s BLE scan)...")
        try:
            found = elm.find_adapters()
        except Exception as e:
            self.status(f"Scan failed: {err_text(e)}", "error")
            return

        def show():
            t = self.query_one("#adapters", DataTable)
            t.clear()
            t.add_row("DEMO", "simulator", "simulated reference adapter + 2011 Pilot", key="DEMO")
            prev = elm.saved()
            if prev and prev not in {p for p, _, _ in found}:
                t.add_row(prev, elm.kind(elm.normalize(prev)), "saved: the last adapter that answered", key=prev)
            for port, kind, desc in found:
                t.add_row(port or "-", kind, desc + ("  (saved)" if port and port == prev else ""), key=port or None)
            n = sum(1 for p, _, _ in found if p)
            self.status(f"{n} possible adapter(s). Select one, then Connect." if n else
                        "Nothing that looks like an OBD adapter. Lit? Phone app closed? Bluetooth on?",
                        "" if n else "warning")
        self.ui(show)

    @on(DataTable.RowSelected, "#adapters")
    def pick_adapter(self, ev):
        port = ev.row_key.value
        if port:
            self.query_one("#port", Input).value = port

    # --- 2 health -------------------------------------------------------------------

    @on(Button.Pressed, "#health-btn")
    def press_health(self):
        self.do_health()

    @work(thread=True, exclusive=True, group="health")
    def do_health(self):
        if not self.need_car():
            return
        self.status("Reading codes, readiness, misfires, Mode 06 and live values...")
        t = Text()
        h = lambda s: t.append(f"\n{s}\n", style="bold underline")
        try:
            with self.lock:
                car = self.car
                h("Codes and readiness")
                for addr, m in car.modules.items():
                    st = car.status(addr)
                    t.append(f"{m.name} ({addr:02X}): ", style="bold")
                    if st is None:
                        t.append("status (PID 01 01): no answer\n", style="yellow")
                    else:
                        if addr == car.engine:
                            t.append("check engine light ")
                            t.append("ON" if st.mil else "off", style="bold red" if st.mil else "green")
                            t.append(", ")
                        t.append(f"{st.count} code(s) counted\n")
                    for kind in ("stored", "pending", "permanent"):
                        codes = car.codes(addr, kind)
                        if codes is None:
                            t.append(f"  {kind}: no answer\n", style="yellow")
                        elif not codes:
                            t.append(f"  {kind}: none\n", style="dim")
                        else:
                            t.append(f"  {dtc.MODES[kind][1]}:\n")
                            for c in codes:
                                t.append(f"    {c}", style="bold red")
                                t.append(f"  {dtc.describe(c)}\n")
                    if st is not None and st.monitors:
                        t.append("  readiness (an emissions test needs these complete):\n")
                        for name, ok in st.monitors:
                            t.append("    complete    " if ok else "    INCOMPLETE  ", style="green" if ok else "bold yellow")
                            t.append(name + "\n")
                extra = car.read(["DISTANCE_SINCE_DTC_CLEAR", "WARMUPS_SINCE_DTC_CLEAR", "DISTANCE_W_MIL"])
                for name, v in extra.items():
                    if v is not None:
                        unit = "" if name == "WARMUPS_SINCE_DTC_CLEAR" else " mi"
                        t.append(f"{pids.BY_NAME[name].desc}: {fmt(v)}{unit}\n", style="dim")

                h("Misfires (Mode 06; Honda numbering 1-3 rear bank, 4-6 front bank, unverified)")
                mf = car.misfires()
                if not mf:
                    t.append("  The engine module didn't answer the misfire monitors. Some cars only fill these "
                             "after a full drive cycle.\n", style="yellow")
                else:
                    counts = {c: (mf[c].get("this / last drive cycle") or 0) for c in mf}
                    top = max(counts.values()) or 1
                    for cyl in range(1, 7):
                        if cyl not in mf:
                            t.append(f"  cyl {cyl}: no answer\n", style="yellow")
                            continue
                        cur, avg = mf[cyl].get("this / last drive cycle"), mf[cyl].get("average (last 10 drive cycles)")
                        bar = "#" * round(20 * (cur or 0) / top) if counts[cyl] else ""
                        t.append(f"  cyl {cyl}: this/last {fmt(cur):>6}  10-drive avg {fmt(avg):>6}  ")
                        t.append(bar + "\n", style="red")
                    if max(counts.values()) > 0:
                        worst = max(counts, key=counts.get)
                        t.append(f"  Highest: cylinder {worst}. Uneven counts on one cylinder are worth following up "
                                 "(plug, coil, injector).\n", style="italic")

                h("Mode 06 tests outside their limits")
                bad = 0
                for mid in sorted(car.mids()):
                    for test in car.monitor(mid) or []:
                        if not test.passed:
                            bad += 1
                            t.append(f"  {monitors.mid_name(mid)} {test.name}: {fmt(test.value)} {test.unit} "
                                     f"(limits {fmt(test.min)}..{fmt(test.max)}) FAIL\n", style="bold red")
                if not bad:
                    t.append("  none: every result is within the limits the engine computer set for it\n", style="green")

                h("Live values (one snapshot)")
                v = car.read(REPORT_LIVE)
                for n in REPORT_LIVE:
                    if v[n] is not None:
                        t.append(f"  {pids.BY_NAME[n].label:16} {fmt(v[n])}\n")
                for b in ("1", "2"):
                    s, lt = v.get(f"SHORT_FUEL_TRIM_{b}"), v.get(f"LONG_FUEL_TRIM_{b}")
                    if s is not None and lt is not None:
                        tot = s + lt
                        t.append(f"  bank {b} total trim {tot:+.1f} %", style="yellow" if abs(tot) > 10 else "")
                        t.append("  (sustained beyond about +/-10 % is worth a look; one snapshot isn't sustained)\n",
                                 style="dim")

                if car.elm.is_29bit:
                    h("Honda reads (candidate decodes, cross-check before trusting)")
                    for cand in honda.CANDIDATES:
                        val, p, refused = car.honda(cand)
                        t.append(f"  {cand['label']:28} {fmt(val) if refused is None else nrc_text(refused)}\n")
        except Exception as e:
            t.append(f"\nStopped: {err_text(e)}\n", style="bold red")
            self.status(f"Health check stopped: {err_text(e)}", "error")
        else:
            self.status(f"Health check done ({time.strftime('%H:%M:%S')}).")
        self.ui(self.query_one("#healthout", Static).update, t)

    @on(Button.Pressed, "#clear-btn")
    def press_clear(self):
        if not self.need_car():
            return

        def done(ok):
            if ok:
                self.do_clear()
            else:
                self.status("Not cleared.")
        self.push_screen(ClearCodes(), done)

    @work(thread=True, exclusive=True, group="health")
    def do_clear(self):
        self.status("Clearing codes (Mode 04 to every module)...", "warning")
        try:
            with self.lock:
                ok = self.car.clear_codes()
                mods = self.car.modules
        except Exception as e:
            self.status(f"Clear failed: {err_text(e)}", "error")
            return
        names = ", ".join(mods[a].name if a in mods else f"{a:02X}" for a in sorted(ok))
        self.status(f"Clear sent. Confirmed by: {names or 'nobody'}. Refresh to see what's left.", "warning")

    # --- 3 live and logging ---------------------------------------------------------

    @on(Button.Pressed, "#live-btn")
    def press_live(self):
        if self.live_running:
            self.stop_live()
            return
        if not self.need_car():
            return
        names = logger.SETS[self.query_one("#pidset", Select).value]
        self.lg = LockedLogger(self, self.car, names, self.query_one("#honda", Switch).value, reopen=self.reopen,
                               quiet=True)
        if not self.lg.columns:
            self.status("None of those PIDs are supported by the engine module.", "error")
            return
        lt = self.query_one("#livetable", DataTable)
        lt.clear()
        for n in self.lg.columns:
            lt.add_row(logger.label(n), "", "", "", key=n)
        self.minmax = {}
        for d in self.history.values():
            d.clear()
        self.rate.clear()
        self.live_stop.clear()
        self.live_running = True
        self.query_one("#live-btn", Button).label = "Stop live"
        self.query_one("#rec-btn", Button).disabled = False
        self.query_one("#pidset", Select).disabled = True
        self.query_one("#honda", Switch).disabled = True
        if self.lg.skipped:
            self.status(f"Not answered by this engine computer, skipped: {', '.join(self.lg.skipped)}")
        self.run_live()

    def stop_live(self):
        if self.live_running:
            self.live_stop.set()

    @work(thread=True, exclusive=True, group="live")
    def run_live(self):
        lg = self.lg

        def each_row(cells):
            self.rate.append(time.monotonic())
            self.call_from_thread(self.show_row, cells)
            return self.live_stop.is_set()

        why = "error"
        try:
            why = lg.run(self.sink, self.markers, stop_when=each_row, link_patience=30)
        except Exception as e:
            self.status(f"Live stopped: {err_text(e)}", "error")
        finally:
            self.car = lg.car
            self.call_from_thread(self.live_ended, why)

    def show_row(self, cells):
        lt = self.query_one("#livetable", DataTable)
        for n, v in cells.items():
            if n not in self.lg.columns:
                continue
            lt.update_cell(n, "now", fmt(v))
            if isinstance(v, (int, float)):
                lo, hi = self.minmax.get(n, (v, v))
                lo, hi = min(lo, v), max(hi, v)
                self.minmax[n] = (lo, hi)
                lt.update_cell(n, "min", fmt(lo))
                lt.update_cell(n, "max", fmt(hi))
        for n, lab in SPARKS:
            v = cells.get(n)
            if isinstance(v, (int, float)):
                self.history[n].append(v)
                self.query_one(f"#sp-{n}", Sparkline).data = list(self.history[n])
                self.query_one(f"#sl-{n}", Label).update(f"{lab}  {fmt(v)}")
        rps = (len(self.rate) - 1) / (self.rate[-1] - self.rate[0]) if len(self.rate) > 1 else 0
        st = self.lg.car.elm.stats
        csv = self.sink.csv
        rec = f"[b red]REC[/] {os.path.basename(csv.path)} {csv.rows} rows  " if csv else ""
        self.query_one("#livestat", Static).update(
            f"{rec}{rps:.1f} rows/s   link: {st['requests']} requests, {st['retries']} retried, "
            f"{st['timeouts']} timeouts")

    def live_ended(self, why):
        self.live_running = False
        self.stop_recording()
        self.query_one("#live-btn", Button).label = "Start live"
        self.query_one("#rec-btn", Button).disabled = True
        self.query_one("#pidset", Select).disabled = False
        self.query_one("#honda", Switch).disabled = False
        if why == "link":
            self.status("Live stopped: the link didn't come back within 30 s. Connect again on tab 1.", "error")
        elif why == "stopped":
            self.status("Live stopped.")

    @on(Button.Pressed, "#rec-btn")
    def press_rec(self):
        if self.sink.csv:
            self.stop_recording()
            return
        tag = self.query_one("#tag", Input).value.strip().replace(" ", "-") or None
        self.markers.take()  # drop anything typed before recording
        path = self.sink.start(self.lg.columns, tag, self.log_dir)
        self.query_one("#rec-btn", Button).label = "Stop CSV"
        self.query_one("#rec-btn", Button).variant = "error"
        self.query_one("#marker", Input).disabled = False
        self.status(f"Recording to {path}. Press m (or type a word in Marker + Enter) when you hear something.")

    def stop_recording(self):
        c = self.sink.stop()
        b = self.query_one("#rec-btn", Button)
        b.label, b.variant = "Record CSV", "success"
        self.query_one("#marker", Input).disabled = True
        if c:
            self.status(f"Saved {c.path}: {c.rows} rows. Tab 7 to summarize it.")
            self.scan_logs()

    def action_marker(self):
        if self.sink.csv:
            self.markers.add()
            self.status(f"marker 'mark{self.markers.n}' dropped")
        else:
            self.status("Markers go into a CSV: Start live, then Record CSV.", "warning")

    @on(Input.Submitted, "#marker")
    def typed_marker(self, ev):
        if self.sink.csv:
            self.markers.add(ev.value.strip() or None)
            self.status(f"marker '{ev.value.strip() or 'mark' + str(self.markers.n)}' dropped")
        ev.input.value = ""

    # --- 4 mode 06 ------------------------------------------------------------------

    @on(Button.Pressed, "#mode06-btn")
    def press_mode06(self):
        self.do_mode06()

    @work(thread=True, exclusive=True, group="mode06")
    def do_mode06(self):
        if not self.need_car():
            return
        self.status("Reading every Mode 06 monitor...")
        rows, bad = [], 0
        try:
            with self.lock:
                for mid in sorted(self.car.mids()):
                    tests = self.car.monitor(mid)
                    if tests is None:
                        rows.append((monitors.mid_name(mid), "no answer", "", "", "", "", Text("-", style="dim")))
                        continue
                    for t in tests:
                        bad += not t.passed
                        rows.append((monitors.mid_name(mid), t.name, fmt(t.value), t.unit, fmt(t.min), fmt(t.max),
                                     Text("pass", style="green") if t.passed else Text("FAIL", style="bold red")))
        except Exception as e:
            self.status(f"Mode 06 stopped: {err_text(e)}", "error")

        def show():
            tb = self.query_one("#mode06table", DataTable)
            tb.clear()
            for r in rows:
                tb.add_row(*r)
            self.query_one("#mode06stat", Static).update(
                f"{len(rows)} results, {bad} outside limits. Honda test IDs (80+) aren't published: named by number.")
        self.ui(show)
        self.status("Mode 06 read." if not bad else f"Mode 06: {bad} result(s) outside limits.", "" if not bad else "warning")

    # --- 5 info ---------------------------------------------------------------------

    @on(Button.Pressed, "#info-btn")
    def press_info(self):
        self.do_info()

    @work(thread=True, exclusive=True, group="info")
    def do_info(self):
        if not self.need_car():
            return
        self.status("Reading VIN, module names, calibration IDs, supported PIDs...")
        t = Text()
        try:
            with self.lock:
                car = self.car
                t.append("VIN: ", style="bold")
                t.append(f"{car.vin() or '(no answer)'}\n\n")
                for addr, m in car.modules.items():
                    name, cals = car.identity(addr)
                    t.append(f"{addr:02X} {name or m.name}", style="bold")
                    t.append(f"   calibration {', '.join(cals)}\n" if cals else "\n")
                    t.append(f"  {len(m.pids)} Mode 01 PIDs:\n")
                    for p in sorted(m.pids):
                        known = pids.BY_PID.get(p)
                        names = ", ".join(x.name for x in known) if known else pids.SPECIAL.get(p, "(not in yf's PID table)")
                        t.append(f"    {p:02X}  {names}\n", style="" if known else "dim")
                    t.append("\n")
                locs = car.o2_locations()
                if locs:
                    t.append(f"O2 sensors fitted (PID 13): {', '.join(locs)}\n")
        except Exception as e:
            t.append(f"\nStopped: {err_text(e)}\n", style="bold red")
        self.ui(self.query_one("#infoout", Static).update, t)
        self.status("Info read.")

    # --- 6 console ------------------------------------------------------------------

    @on(Switch.Changed, "#raw")
    def toggle_raw(self, ev):
        self.raw_on = ev.value

    @on(Button.Pressed, "#query-btn")
    @on(Input.Submitted, "#query")
    def press_query(self):
        text = self.query_one("#query", Input).value.split()
        if not text:
            return
        if not self.need_car():
            return
        module = text[1] if len(text) > 1 else self.query_one("#module", Select).value
        self.do_query(text[0], module)

    @work(thread=True, group="query")
    def do_query(self, hexreq, module):
        out = self.query_one("#queryout", RichLog)
        w = lambda s: self.call_from_thread(out.write, s)
        try:
            with self.lock:
                addr = self.car.module(module)
                if addr is None:
                    w(f"No module '{module}' answered.")
                    return
                r = self.car.query(hexreq, addr)
        except ValueError as e:
            w(f"refused: {e}")
            return
        except Exception as e:
            w(f"error: {err_text(e)}")
            return
        w(f"> {hexreq.upper()} to {addr:02X} {honda.module_name(addr)}")
        for ln in r.lines:
            w(f"  < {ln}")
        p = r.get(addr)
        if p is None:
            w(f"  {addr:02X}: {r.status or 'no answer'}" + (" (frames missing)" if addr in r.broken else ""))
        elif p[0] == 0x7F:
            w(f"  {addr:02X}: {nrc_text(p[2]) if len(p) > 2 else p.hex(' ')}")
        else:
            w(f"  {addr:02X}: {len(p)} bytes: {p.hex(' ').upper()}")

    # --- 7 logs ---------------------------------------------------------------------

    @on(Button.Pressed, "#logs-btn")
    def scan_logs(self):
        t = self.query_one("#loglist", DataTable)
        t.clear()
        try:
            files = sorted((f for f in os.listdir(self.log_dir) if f.endswith(".csv")), reverse=True)
        except FileNotFoundError:
            files = []
        for f in files:
            p = os.path.join(self.log_dir, f)
            st = os.stat(p)
            t.add_row(f, f"{st.st_size / 1024:.0f}", time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                      key=p)
        if not files:
            self.query_one("#summaryout", Static).update("No logs yet. Tab 3: Start live, then Record CSV.")

    @on(DataTable.RowSelected, "#loglist")
    def pick_log(self, ev):
        self.summarize(ev.row_key.value)

    @work(thread=True, exclusive=True, group="summary")
    def summarize(self, path):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summarize.py")
        try:
            r = subprocess.run([sys.executable, script, path], capture_output=True, text=True, timeout=60)
            text = r.stdout + (f"\n{r.stderr}" if r.returncode else "")
        except Exception as e:
            text = f"Couldn't summarize: {err_text(e)}"
        self.ui(self.query_one("#summaryout", Static).update, Text(text))

    # --- shutdown -------------------------------------------------------------------

    def on_unmount(self):
        self.live_stop.set()
        self.sink.stop()
        if self.car:
            try:
                self.car.close()
            except Exception:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="default: the saved adapter (.adapter) if there is one, else DEMO. "
                    "Or auto, DEMO, a serial path, ble, ble:<address>, "
                    "or socket://<ip>:<port>")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait per reply (default 1.0)")
    ap.add_argument("--baud", type=int, help="USB/serial adapters: skip the baud-rate probe")
    ap.add_argument("--protocol", help="force the ELM327 protocol (the Pilot needs none: it auto-detects 7)")
    ap.add_argument("--log-dir", help="where logs go (default logs)")
    ap.add_argument("--connect", action="store_true", help="connect on start")
    a = ap.parse_args(argv)
    YfTui(a.port or elm.saved() or "DEMO", a.timeout, a.protocol, a.baud, a.log_dir, connect=a.connect).run()


if __name__ == "__main__":
    main()
