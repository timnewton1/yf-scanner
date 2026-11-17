"""pyserial URL handler: serial.serial_for_url("sim://pilot[?clean&drop=0.1&strict]").

The simulated reference adapter (sim/elm.py) in front of a simulated 2011 Pilot (sim/pilot.py),
so `--port DEMO` runs through exactly the code a real adapter does.

    sim://pilot          DEMO: one stored code, a misfiring cylinder 3, EVAP incomplete
    sim://pilot?clean    the car as measured: no codes, all monitors complete
    drop=<0..1>          chance a multi-frame reply loses a frame (BLE behavior)
    latency=<s>          seconds per OBD request (default 0.15, like the real BLE link; 0 for tests)
    strict               a genuine ELM327 (? to unknown commands, every broadcast reply returned)
"""

import threading
import urllib.parse

from serial.serialutil import PortNotOpenError, SerialBase, SerialException


class Serial(SerialBase):
    def open(self):
        from sim.elm import ElmEmulator
        from sim.pilot import Pilot

        if self.is_open:
            raise SerialException("Port is already open.")
        rest = self._port.split("://", 1)[1]
        name, _, query = rest.partition("?")
        if name not in ("pilot", ""):
            raise SerialException(f"unknown simulator '{name}' (sim://pilot)")
        opts = urllib.parse.parse_qs(query, keep_blank_values=True)
        strict = "strict" in opts
        self.sim = ElmEmulator(Pilot(demo="clean" not in opts), clone=not strict, first_reply_only=not strict,
                               drop_rate=float(opts.get("drop", ["0"])[0]),
                               latency=float(opts.get("latency", ["0.15"])[0]))
        self._out = bytearray()
        self._cv = threading.Condition()
        self.broken = False  # set True to act like a BLE link that just dropped
        self.is_open = True

    def close(self):
        self.is_open = False

    def _reconfigure_port(self, force_update=False):
        pass

    @property
    def in_waiting(self):
        if not self.is_open:
            raise PortNotOpenError()
        return len(self._out)

    def read(self, size=1):
        if not self.is_open:
            raise PortNotOpenError()
        if self.broken:
            raise SerialException("simulated link dropped")
        with self._cv:
            if not self._out:
                self._cv.wait(self._timeout or 0)
            data = bytes(self._out[:size])
            del self._out[:size]
            return data

    def write(self, data):
        if not self.is_open:
            raise PortNotOpenError()
        if self.broken:
            raise SerialException("simulated link dropped")
        out = self.sim.feed(bytes(data))
        with self._cv:
            self._out.extend(out)
            self._cv.notify_all()
        return len(data)

    def reset_input_buffer(self):
        with self._cv:
            self._out.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass
