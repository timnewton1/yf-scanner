"""The reference adapter type (elm.REFERENCE, the ELM327 v1.5 BLE mini) at the byte level, for tests:
feed it bytes, it answers like that clone's firmware on a CAN car. The BLE side lives in test_elm.py.

Only what python-OBD's connect and a couple of PIDs need. Values are made up.
"""


class ElmSim:
    def __init__(self, version="ELM327 v1.5"):
        self.version = version
        self.echo = True
        self.headers = False
        self.pending = b""
        self.sent = []  # every command received, for assertions

    def feed(self, data):
        """Bytes in from the host -> bytes the adapter sends back (possibly empty)."""
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
        reply = self.reply(cmd)
        return echo + reply.encode() + b"\r\r>"

    def reply(self, cmd):
        if cmd in ("", "\x7f\x7f"):
            return "?"
        if cmd == "ATZ":
            self.echo, self.headers = True, False
            return "\r\r" + self.version
        if cmd == "ATI":
            return self.version
        if cmd == "AT@1":
            return "OBDII to RS232 Interpreter"
        if cmd == "STI":
            return "?"
        if cmd == "ATRV":
            return "12.6V"
        if cmd == "ATE0":
            self.echo = False
            return "OK"
        if cmd in ("ATH1", "ATH0"):
            self.headers = cmd == "ATH1"
            return "OK"
        if cmd.startswith(("ATL", "ATSP", "ATTP", "ATSH", "ATS", "ATAT", "ATST")):
            return "OK"
        if cmd == "ATDPN":
            return "A6"
        if cmd == "ATDP":
            return "AUTO, ISO 15765-4 (CAN 11/500)"
        frames = {
            "0100": "06 41 00 BE 3F A8 13",
            "010C": "04 41 0C 1A F8",  # 1726 rpm
            "010D": "03 41 0D 32",  # 50 km/h
        }
        if cmd in frames:
            return ("7E8 " if self.headers else "") + frames[cmd]
        if cmd.startswith("AT"):
            return "?"
        return "NO DATA"
