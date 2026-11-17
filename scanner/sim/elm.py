"""A byte-level ELM327, the reference clone (ELM327 v1.5 BLE mini) with its measured quirks:

- answers OK to AT/ST commands it doesn't implement (a real ELM says ?)
- to a broadcast that two modules answer, usually returns only one reply (first_reply_only)
- over BLE, drops a consecutive frame now and then (drop_rate)

Feed it bytes, it answers like the adapter does. The car behind it is any object with
.protocol ("7") and .respond(module, payload) -> bytes or None, plus .modules (addresses).
"""

import random
import time

CAN_29 = {"7", "9"}


class ElmEmulator:
    def __init__(self, car, version="ELM327 v1.5", clone=True, first_reply_only=True, drop_rate=0.0, seed=1,
                 latency=0.0):
        """latency: seconds each OBD request takes (the real BLE link: about 0.15)."""
        self.car = car
        self.latency = latency
        self.version = version
        self.clone = clone
        self.first_reply_only = first_reply_only
        self.drop_rate = drop_rate
        self.rng = random.Random(seed)
        self.pending = b""
        self.sent = []  # every command received, for assertions
        self._defaults()

    def _defaults(self):
        self.echo, self.linefeeds, self.spaces, self.headers = True, False, True, False
        self.protocol = "0"        # 0 = auto, not searched yet
        self.header = None         # None = the protocol's default (broadcast)

    # --- bytes in, bytes out ----------------------------------------------------------

    def feed(self, data):
        self.pending += data
        out = b""
        while b"\r" in self.pending:
            line, self.pending = self.pending.split(b"\r", 1)
            out += self.command(line)
        return out

    def command(self, raw):
        cmd = raw.decode(errors="ignore").replace(" ", "").upper()
        self.sent.append(cmd)
        echo = raw + b"\r" if self.echo else b""
        nl = "\r\n" if self.linefeeds else "\r"
        reply = self.reply(cmd).replace("\r", nl)
        return echo + reply.encode() + (nl * 2).encode() + b">"

    # --- AT commands --------------------------------------------------------------------

    def _unknown(self):
        return "OK" if self.clone else "?"

    def reply(self, cmd):
        cmd = cmd.replace("\x7f", "")
        if cmd == "":
            return "?"
        if cmd.startswith("ST"):
            return self._unknown()
        if not cmd.startswith("AT"):
            return self.obd(cmd)
        a = cmd[2:]
        if a in ("Z", "WS"):
            self._defaults()
            return "\r" + self.version
        if a == "D":
            self._defaults()
            return "OK"
        if a == "I":
            return self.version
        if a == "@1":
            return "OBDII to RS232 Adapter"
        if a == "RV":
            return f"{self.car.battery_volts():.1f}V"
        if a in ("E0", "E1"):
            self.echo = a == "E1"
            return "OK"
        if a in ("L0", "L1"):
            self.linefeeds = a == "L1"
            return "OK"
        if a in ("S0", "S1"):
            self.spaces = a == "S1"
            return "OK"
        if a in ("H0", "H1"):
            self.headers = a == "H1"
            return "OK"
        if a.startswith("SP") or a.startswith("TP"):
            p = a[2:].lstrip("A") or "0"
            self.protocol = p
            self.header = None
            return "OK"
        if a == "DPN":
            if self.protocol == "0":
                return "A0"
            return ("A" if self.auto else "") + self.protocol
        if a == "DP":
            names = {"7": "ISO 15765-4 (CAN 29/500)", "6": "ISO 15765-4 (CAN 11/500)", "0": "AUTO"}
            return ("AUTO, " if self.auto and self.protocol != "0" else "") + names.get(self.protocol, "?")
        if a.startswith("SH"):
            self.header = a[2:]
            return "OK"
        if a.startswith(("AT", "ST", "CAF", "CRA", "CP", "CF", "CM", "M", "R", "AL", "NL")):
            return "OK"
        return self._unknown()

    auto = False

    # --- OBD requests -------------------------------------------------------------------

    def obd(self, cmd):
        try:
            payload = bytes.fromhex(cmd)
        except ValueError:
            return "?"
        if self.latency:
            time.sleep(self.latency)
        prefix = ""
        if self.protocol == "0":
            prefix = "SEARCHING...\r"
            self.protocol = self.car.protocol
            self.auto = True
        if self.protocol != self.car.protocol:
            return prefix + ("NO DATA" if self.protocol in ("6", "7", "8", "9") else "UNABLE TO CONNECT")
        is29 = self.protocol in CAN_29
        header = self.header or ("DB33F1" if is29 else "7DF")
        if is29:
            header = header[-6:].rjust(6, "0")
            functional = header[:2] == "DB"
            target = None if functional else int(header[2:4], 16)
        else:
            functional = header == "7DF"
            target = None if functional else int(header, 16) + 8
        mods = list(self.car.modules) if functional else [target]
        answers = []
        for m in mods:
            if m not in self.car.modules:
                continue
            p = self.car.respond(m, payload)
            if p is not None:
                answers.append((m, p))
        if functional and self.first_reply_only and len(answers) > 1 and self.rng.random() < 0.85:
            answers = [self.rng.choice(answers)]
        if not answers:
            return prefix + "NO DATA"
        lines = []
        for m, p in answers:
            lines += self.frames(m, p, is29)
        return prefix + "\r".join(lines)

    def frames(self, module, p, is29):
        head = f"18DAF1{module:02X}" if is29 else f"{module:03X}"
        if len(p) <= 7:
            frames = [bytes([len(p)]) + p]
        else:
            frames = [bytes([0x10 | (len(p) >> 8), len(p) & 0xFF]) + p[:6]]
            rest, seq = p[6:], 1
            while rest:
                chunk = rest[:7]
                rest = rest[7:]
                frames.append(bytes([0x20 | (seq & 0x0F)]) + chunk + b"\x55" * (7 - len(chunk)))
                seq += 1
            if self.drop_rate and len(frames) > 2 and self.rng.random() < self.drop_rate:
                del frames[self.rng.randrange(1, len(frames) - 1)]
        out = []
        for f in frames:
            if not self.headers:
                f = f[1:] if f[0] >> 4 == 0 else f
            body = f.hex().upper()
            text = head + body if self.headers else body
            if self.spaces:
                parts = ([head[:2], head[2:4], head[4:6], head[6:8]] if is29 else [head]) if self.headers else []
                text = " ".join(parts + [body[i:i + 2] for i in range(0, len(body), 2)])
            out.append(text)
        return out
