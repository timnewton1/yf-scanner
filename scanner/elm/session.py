"""The ELM327 dialogue: AT commands, OBD requests to one module or all, ISO-TP reassembly.

Talks to any pyserial-style port (USB, rfcomm, Wi-Fi socket, ble://, sim://). CAN only: every
2009-2015 Pilot is CAN, and the older K-line and J1850 protocols format replies differently.

Why this exists instead of python-OBD: on the 2011 Pilot two modules answer OBD requests, 11
(ECM-EngineControl) and 1D (TCM-TransmisCtrl), and the reference adapter returns only one of the
two replies to a broadcast, whichever it heard first. python-OBD guessed "the engine" from the
first reply it saw and then discarded every reply from the real engine computer. Here, requests
can go to one module by its physical address, and replies are kept per module.

Read-only except what the caller sends: nothing in this file sends a request on its own except the
`0100` used to find the protocol at setup.
"""

import re
import time

PROMPT = b">"

# ELM327 protocol numbers (ATSP / ATDPN) that are CAN, and whether they use 29-bit IDs.
CAN_PROTOCOLS = {"6": False, "7": True, "8": False, "9": True}
PROTOCOL_NAMES = {
    "6": "ISO 15765-4 CAN 11-bit 500k", "7": "ISO 15765-4 CAN 29-bit 500k",
    "8": "ISO 15765-4 CAN 11-bit 250k", "9": "ISO 15765-4 CAN 29-bit 250k",
    "1": "SAE J1850 PWM", "2": "SAE J1850 VPW", "3": "ISO 9141-2", "4": "ISO 14230-4 KWP (5 baud)",
    "5": "ISO 14230-4 KWP (fast)", "A": "SAE J1939",
}

# Status lines the ELM327 prints instead of data. Text -> what it means for the caller.
NO_REPLY = ("NO DATA", "NODATA")
FAILURES = ("CAN ERROR", "BUS ERROR", "BUS BUSY", "BUFFER FULL", "DATA ERROR", "RX ERROR", "FB ERROR",
            "LV RESET", "ERR", "STOPPED", "UNABLE TO CONNECT", "ACT ALERT", "?")
HEX_LINE = re.compile(r"^[0-9A-F]+$")

# Negative response codes (ISO 14229 / 15765), for messages.
NRC = {
    0x10: "general reject", 0x11: "service not supported", 0x12: "sub-function not supported",
    0x13: "wrong message length", 0x14: "response too long", 0x22: "conditions not correct",
    0x31: "request out of range (this module doesn't have that identifier)",
    0x33: "security access denied", 0x78: "response pending", 0x7E: "not supported in this session",
    0x7F: "service not supported in this session",
}

FUNCTIONAL = None  # `to=FUNCTIONAL`: broadcast to every OBD module


class ElmError(Exception):
    """The adapter didn't answer, or answered with an error."""


class LinkLost(ElmError):
    """The port itself failed (BLE dropped, cable pulled)."""


class NoPrompt(ElmError):
    """The adapter didn't finish a reply in time. The link may still be fine."""


class Reply(dict):
    """One request's replies: {module address: payload bytes}. Plus what went wrong, if anything.

    payload is the reassembled ISO-TP message, starting with the response service byte (0x41 for
    Mode 01, 0x7F for a refusal)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.status = ""      # "NO DATA", "CAN ERROR", ... when the adapter said so
        self.broken = []      # module addresses whose multi-frame reply arrived with frames missing
        self.lines = []       # the raw lines, for --raw and bug reports

    def positive(self, service):
        """Only the modules that answered with service + 0x40."""
        return {a: p for a, p in self.items() if p and p[0] == service + 0x40}

    def refused(self):
        """{module: NRC byte} for modules that answered 7F <service> <code>."""
        return {a: p[2] for a, p in self.items() if len(p) >= 3 and p[0] == 0x7F}


def nrc_text(code):
    return f"7F {code:02X}: {NRC.get(code, 'refused')}"


class Elm:
    def __init__(self, port, timeout=1.0, log=None):
        """port: an open pyserial-style object. timeout: seconds to wait for one reply.
        log: optional callable(direction, text) that sees every line sent and received."""
        self.port = port
        self.timeout = timeout
        self.log = log
        self.protocol = None      # ELM protocol number as a string, "7" on the Pilot
        self.header = None        # the ATSH currently in effect
        self.version = ""         # ATZ / ATI answer
        self.stats = {"requests": 0, "retries": 0, "timeouts": 0}  # link quality, for log summaries

    # --- the byte level -------------------------------------------------------------

    def _write(self, text):
        if self.log:
            self.log(">", text)
        try:
            self.port.reset_input_buffer()
            self.port.write(text.encode("ascii") + b"\r")
        except Exception as e:
            raise LinkLost(f"write failed: {e}") from e

    def _read(self, timeout):
        """Bytes up to the '>' prompt. Raises ElmError on timeout."""
        buf = bytearray()
        end = time.monotonic() + timeout
        while PROMPT not in buf:
            try:
                chunk = self.port.read(self.port.in_waiting or 1)
            except Exception as e:
                raise LinkLost(f"read failed: {e}") from e
            if chunk:
                buf.extend(chunk)
            elif time.monotonic() > end:
                self.stats["timeouts"] += 1
                raise NoPrompt(f"no prompt from the adapter within {timeout:.1f} s (got {bytes(buf)!r})")
        return bytes(buf[:buf.index(PROMPT)])

    def send(self, cmd, timeout=None):
        """Send one command line; return the reply lines (no echo, no blanks, no prompt)."""
        self._write(cmd)
        raw = self._read(timeout or self.timeout)
        text = raw.replace(b"\x00", b"").decode("ascii", errors="replace")
        lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
        if lines and lines[0].replace(" ", "").upper() == cmd.replace(" ", "").upper():
            lines = lines[1:]  # echo, if ATE0 hasn't taken yet
        if self.log:
            for ln in lines:
                self.log("<", ln)
        return lines

    def at(self, cmd, timeout=None):
        """An AT/ST command; the reply lines joined with ' / '. Don't use for ATSH or ATSP:
        the session tracks those (see target() and setup())."""
        return " / ".join(self.send(cmd, timeout))

    # --- setup ------------------------------------------------------------------------

    def reset(self):
        """ATZ and the settings every request below relies on."""
        lines = self.send("ATZ", timeout=max(3.0, self.timeout))
        self.version = next((ln for ln in lines if "ELM" in ln.upper() or "STN" in ln.upper()), " ".join(lines))
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1"):
            self.at(cmd)
        self.header = None
        self.protocol = None

    def setup(self, protocol=None, search_timeout=15.0):
        """Pick the OBD protocol and check the car answers. protocol: ELM number ("7") or None to
        auto-detect. Returns True if at least one module answered 0100.

        Unlike python-OBD, a forced protocol only counts as connected if the car really answers on it."""
        self.reset()
        self.at(f"ATSP{protocol or '0'}")
        lines = self.send("0100", search_timeout)
        dpn = self.at("ATDPN").upper().lstrip("A")
        self.protocol = dpn[:1] if dpn else (protocol or None)
        answered = bool(self.parse(lines).positive(0x01)) if self.is_can else False
        if not answered:
            self.protocol = None if protocol is None else self.protocol
        return answered

    @property
    def is_can(self):
        return self.protocol in CAN_PROTOCOLS

    @property
    def is_29bit(self):
        return CAN_PROTOCOLS.get(self.protocol, False)

    @property
    def protocol_name(self):
        return PROTOCOL_NAMES.get(self.protocol or "", f"protocol {self.protocol}")

    def voltage(self):
        m = re.search(r"(\d+(\.\d+)?)\s*V", self.at("ATRV").upper())
        return float(m.group(1)) if m else None

    # --- addressing -------------------------------------------------------------------

    def header_for(self, to):
        """ATSH value for a request to module `to` (None = broadcast). Modules are named by the
        address they answer from: one byte on 29-bit (0x11), the 11-bit response ID (0x7E8) otherwise."""
        if self.is_29bit:
            return "DB33F1" if to is None else f"DA{to:02X}F1"
        return "7DF" if to is None else f"{to - 8:03X}"

    def target(self, to):
        h = self.header_for(to)
        if h != self.header:
            self.at(f"ATSH{h}")
            self.header = h

    # --- requests ---------------------------------------------------------------------

    def request(self, data, to=FUNCTIONAL, timeout=None, retries=2, retry_silent=False):
        """Send one OBD/UDS request (bytes or hex string) to module `to`, or to all.
        Returns a Reply. Retries (twice by default) if a multi-frame reply came back with frames
        missing: on the reference adapter's BLE link about one long reply in five loses a frame.
        retry_silent: also retry on NO DATA, for requests the module is known to answer (status, codes)."""
        if not self.is_can:
            raise ElmError("not connected on a CAN protocol (setup() first; only CAN is supported)")
        hexcmd = data.hex().upper() if isinstance(data, (bytes, bytearray)) else data.replace(" ", "").upper()
        self.target(to)
        self.stats["requests"] += 1
        for attempt in range(retries + 1):
            if attempt:
                self.stats["retries"] += 1
            lines = self.send(hexcmd, timeout)
            reply = self.parse(lines)
            silent = retry_silent and not reply and not reply.broken
            if (not reply.broken and not silent) or attempt == retries:
                return reply
        return reply

    # --- parsing ----------------------------------------------------------------------

    def _frames(self, lines):
        """(sender, frame bytes) for each data line; status lines are skipped."""
        hlen = 8 if self.is_29bit else 3
        out = []
        for ln in lines:
            s = ln.replace(" ", "").upper()
            if s.startswith("SEARCHING"):
                continue
            if not HEX_LINE.match(s) or len(s) <= hlen or (len(s) - hlen) % 2:
                continue
            head, body = s[:hlen], bytes.fromhex(s[hlen:])
            sender = int(head[-2:], 16) if hlen == 8 else int(head, 16)
            out.append((sender, body))
        return out

    def parse(self, lines):
        reply = Reply()
        reply.lines = list(lines)
        joined = " ".join(lines).upper().replace(" ", "")
        for word in NO_REPLY:
            if word.replace(" ", "") in joined:
                reply.status = "NO DATA"
        for word in FAILURES:
            if any(ln.upper().strip() == word or ln.upper().startswith(word) for ln in lines):
                reply.status = word
        msgs = {}  # sender -> list of finished payloads
        partial = {}  # sender -> [expected length, bytearray, next sequence number]
        for sender, f in self._frames(lines):
            if not f:
                continue
            kind = f[0] >> 4
            if kind == 0:  # single frame
                n = f[0] & 0x0F
                msgs.setdefault(sender, []).append(bytes(f[1:1 + n]))
            elif kind == 1 and len(f) >= 2:  # first frame
                n = ((f[0] & 0x0F) << 8) | f[1]
                partial[sender] = [n, bytearray(f[2:]), 1]
            elif kind == 2 and sender in partial:  # consecutive frame
                p = partial[sender]
                if p is None:
                    continue
                if (f[0] & 0x0F) != p[2] & 0x0F:
                    partial[sender] = None  # a frame went missing
                    reply.broken.append(sender)
                    continue
                p[1].extend(f[1:])
                p[2] += 1
                if len(p[1]) >= p[0]:
                    msgs.setdefault(sender, []).append(bytes(p[1][:p[0]]))
                    del partial[sender]
            # kind 3 (flow control) is the adapter's business
        for sender, p in partial.items():
            if p is not None:
                reply.broken.append(sender)  # ran out of frames
        for sender, payloads in msgs.items():
            # a module that said "response pending" (7F xx 78) and then answered: keep the answer
            final = [p for p in payloads if not (len(p) >= 3 and p[0] == 0x7F and p[2] == 0x78)]
            reply[sender] = (final or payloads)[-1]
        return reply

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass
