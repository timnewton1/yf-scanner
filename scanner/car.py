"""The car, as the scanner sees it: which modules answer, what each supports, and read-only queries
addressed to one module at a time.

Every request goes to a module's physical address once the modules are known. Broadcasts are
only used to find them, because the reference adapter returns just one reply to a broadcast when
two modules answer (see elm/session.py).
"""

from . import elm, honda
from .elm.session import ElmError, LinkLost  # noqa: F401  (re-exported for callers)
from .obd2 import dtc, monitors, pids
from .obd2.status import Status

MAX_PIDS_PER_REQUEST = 6  # ISO 15765-4 allows up to 6 PIDs in one Mode 01 request

# Services the --query option may send. All are reads; nothing here can change the car.
READ_ONLY_SERVICES = {0x01, 0x02, 0x03, 0x06, 0x07, 0x09, 0x0A, 0x22}


class Module:
    def __init__(self, addr, name):
        self.addr, self.name = addr, name
        self.pids = set()       # Mode 01 PIDs it says it supports
        self.mids = None        # Mode 06 monitor IDs (filled on first use)
        self.info = None        # Mode 09 PIDs
        self.batch = True       # whether it accepts several PIDs per request

    def __repr__(self):
        return f"Module({self.addr:02X} {self.name}, {len(self.pids)} PIDs)"


class Car:
    def __init__(self, adapter):
        self.adapter = adapter
        self.elm = adapter.elm
        self.modules = {}

    # --- connecting -----------------------------------------------------------------

    @classmethod
    def open(cls, port="auto", timeout=1.0, protocol=None, baud=None, log=None):
        """Connect and discover modules. Raises elm.adapter.NotConnected if no adapter answers;
        check .connected for the car."""
        ad = elm.connect(port, timeout=timeout, protocol=protocol, baud=baud, log=log)
        car = cls(ad)
        if ad.car:
            car.discover()
        return car

    @property
    def connected(self):
        return bool(self.modules)

    def close(self):
        self.adapter.close()

    def known_addresses(self):
        return set(honda.MODULES if self.elm.is_29bit else honda.MODULES_11BIT)

    def discover(self, sweep=False):
        """Find every module that answers Mode 01. Broadcast a few times (replies vary on the
        reference adapter), add the Pilot's known addresses, then confirm each one directly.
        sweep=True also tries every physical address (29-bit: 00-FF, slow: about 0.15 s each)."""
        seen = set()
        for _ in range(3):
            seen |= set(self.elm.request("0100").positive(0x01))
        cands = seen | self.known_addresses()
        if sweep:
            cands |= set(range(0x100)) if self.elm.is_29bit else set(range(0x7E8, 0x7F0))
        self.modules = {}
        for addr in sorted(cands):
            if addr == 0xF1 and self.elm.is_29bit:
                continue  # the tester's own address
            m = Module(addr, honda.module_name(addr))
            m.pids = self._supported(addr, 0x01)
            if m.pids:
                self.modules[addr] = m
        return self.modules

    def _supported(self, addr, mode):
        """Chain the 'supported PIDs' requests (00, 20, 40...) for one module and mode."""
        out = set()
        base = 0
        while base <= 0xE0:
            # past the first range the module has said it has more, so silence is a glitch: retry it
            r = self.elm.request(bytes([mode, base]), to=addr, retry_silent=base > 0).positive(mode)
            p = r.get(addr)
            if not p or len(p) < 6:
                break
            got = pids.bitmask_pids(base, p[2:6])
            out |= set(got)
            if base + 0x20 not in got:
                break
            base += 0x20
        return {p for p in out if p % 0x20}  # drop the "next range" markers

    @property
    def engine(self):
        """The engine module's address: the Pilot's ECM if it answered, else the one with most PIDs."""
        if honda.ECM in self.modules and self.elm.is_29bit:
            return honda.ECM
        if 0x7E8 in self.modules:
            return 0x7E8
        return max(self.modules, key=lambda a: len(self.modules[a].pids)) if self.modules else None

    def module(self, which):
        """'engine', 'transmission', or an address (int or hex string) -> address in self.modules."""
        if which in (None, "engine", "ecm"):
            return self.engine
        if which in ("transmission", "tcm"):
            for a in (honda.TCM, 0x7E9):
                if a in self.modules:
                    return a
            return None
        a = int(which, 16) if isinstance(which, str) else which
        return a

    # --- Mode 01 --------------------------------------------------------------------

    def read(self, names, addr=None):
        """Read Mode 01 values by name -> {name: value or None}. Asks up to 6 PIDs per request.
        Names the module doesn't support come back None."""
        addr = self.engine if addr is None else addr
        m = self.modules.get(addr)
        out = {n: None for n in names}
        want = []
        for n in names:
            p = pids.BY_NAME[n].pid
            if m and p in m.pids and p not in want:
                want.append(p)
        got = {}
        step = MAX_PIDS_PER_REQUEST if (m is None or m.batch) else 1
        for i in range(0, len(want), step):
            chunk = want[i:i + step]
            r = self.elm.request(bytes([0x01] + chunk), to=addr).positive(0x01)
            if addr in r:
                got.update(pids.split_multi(r[addr]))
            elif len(chunk) > 1:
                # no reply to a multi-PID request: fall back to one at a time from now on
                m.batch = False
                for p in chunk:
                    r1 = self.elm.request(bytes([0x01, p]), to=addr).positive(0x01)
                    if addr in r1:
                        got.update(pids.split_multi(r1[addr]))
        for n in names:
            data = got.get(pids.BY_NAME[n].pid)
            if data is not None:
                out[n] = pids.decode(n, data)
        return out

    def status(self, addr=None):
        addr = self.engine if addr is None else addr
        p = self.elm.request("0101", to=addr, retry_silent=True).positive(0x01).get(addr)
        return Status(p) if p and len(p) >= 6 else None

    def o2_locations(self, addr=None):
        """PID 13: which O2 sensor positions exist, e.g. ['B1S1', 'B1S2', 'B2S1', 'B2S2']."""
        addr = self.engine if addr is None else addr
        p = self.elm.request("0113", to=addr).positive(0x01).get(addr)
        if not p or len(p) < 3:
            return []
        return [loc for i, loc in enumerate(pids.O2_LOCATIONS_13) if p[2] & (1 << i)]

    # --- codes ----------------------------------------------------------------------

    def codes(self, addr, kind="stored"):
        """['P0301', ...], or None if the module didn't answer."""
        service = dtc.MODES[kind][0]
        r = self.elm.request(bytes([service]), to=addr, retry_silent=True)
        p = r.positive(service).get(addr)
        return None if p is None else dtc.parse(p)

    def clear_codes(self):
        """Mode 04 to every module. Resets readiness monitors and learned fuel trims.
        Returns the addresses that confirmed (44)."""
        r = self.elm.request("04", timeout=max(self.elm.timeout, 3.0))
        ok = set(r.positive(0x04))
        for addr in self.modules:  # broadcasts lose replies on the reference adapter; ask each too
            if addr not in ok:
                if addr in self.elm.request("04", to=addr, timeout=max(self.elm.timeout, 3.0)).positive(0x04):
                    ok.add(addr)
        return ok

    # --- Mode 06 --------------------------------------------------------------------

    def mids(self, addr=None):
        addr = self.engine if addr is None else addr
        m = self.modules.get(addr)
        if m is not None and m.mids is not None:
            return m.mids
        found = self._supported(addr, 0x06)
        if m is not None:
            m.mids = found
        return found

    def monitor(self, mid, addr=None):
        """[monitors.Test] for one monitor ID, or None if no answer."""
        addr = self.engine if addr is None else addr
        known = self.modules.get(addr) is not None and mid in (self.modules[addr].mids or ())
        p = self.elm.request(bytes([0x06, mid]), to=addr, retry_silent=known).positive(0x06).get(addr)
        return None if p is None else monitors.parse(p)

    def misfires(self, addr=None):
        """{cylinder: {"this / last drive cycle": n, "average (last 10 drive cycles)": n}}.
        Cylinders whose monitor doesn't answer are missing."""
        addr = self.engine if addr is None else addr
        out = {}
        for cyl in range(1, 7):  # asked even if the supported-MID chain broke: six cheap reads
            tests = self.monitor(0xA1 + cyl, addr)
            if tests:
                out[cyl] = {t.name: t.value for t in tests if t.tid in monitors.MISFIRE_TIDS}
        return out

    # --- Mode 09 --------------------------------------------------------------------

    def info(self, pid, addr=None):
        """Raw Mode 09 data items after '49 <pid> <count>', or None."""
        addr = self.engine if addr is None else addr
        p = self.elm.request(bytes([0x09, pid]), to=addr).positive(0x09).get(addr)
        if not p or len(p) < 3:
            return None
        return p[3:]

    def text(self, pid, addr=None):
        d = self.info(pid, addr)
        if d is None:
            return None
        return bytes(b for b in d if 32 <= b < 127).decode("ascii").strip()

    def vin(self):
        return self.text(0x02)

    def identity(self, addr):
        """(name, calibration IDs) a module reports about itself."""
        name = self.text(0x0A, addr)
        cal = self.info(0x04, addr)
        cals = []
        if cal:
            for i in range(0, len(cal), 16):
                s = bytes(b for b in cal[i:i + 16] if 32 <= b < 127).decode("ascii").strip()
                if s:
                    cals.append(s)
        return name, cals

    # --- Honda ----------------------------------------------------------------------

    def honda(self, cand):
        """(value, reply payload or None, refusal code or None) for one Honda candidate."""
        addr = cand["module"] if self.elm.is_29bit else None
        if addr is None:
            return None, None, None
        r = self.elm.request(cand["request"], to=addr, retry_silent=addr in self.modules)
        p = r.get(addr)
        if p is None:
            return None, None, None
        if p[0] == 0x7F:
            return None, p, p[2] if len(p) > 2 else None
        return honda.value(cand, p), p, None

    # --- anything read-only ---------------------------------------------------------

    def query(self, data, addr):
        """A read-only request (services in READ_ONLY_SERVICES) to one module. Returns the Reply."""
        b = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
        if not b or b[0] not in READ_ONLY_SERVICES:
            raise ValueError(f"service {b[:1].hex() or '?'} isn't on the read-only list "
                             f"({', '.join(f'{s:02X}' for s in sorted(READ_ONLY_SERVICES))})")
        return self.elm.request(b, to=addr)
