"""Honda specifics for the 2009-2015 Pilot: which modules answer, and Honda's own Mode 22 reads.

Measured on a 2011 Pilot 4WD (2026-11-16, docs/honda-enhanced.md): OBD runs on 29-bit CAN at
500k only (11-bit gets NO DATA), and two modules answer OBD requests:

    11  ECM-EngineControl     (Mode 09 0A), cal ID 37805-RN0-...   engine PIDs, Mode 06, VIN
    1D  TCM-TransmisCtrl      (Mode 09 0A), cal ID 37806-RN0-...   a few PIDs, its own codes

Mode 22 (ReadDataByIdentifier) is read-only. The candidates below come from owner reports for
other Hondas and are marked "(candidate)" until a decode is cross-checked against something
trusted (ATF: a thermocouple on the cooler line; VCM: the ECO light on a steady cruise).
"""

ECM = 0x11
TCM = 0x1D

MODULES = {
    ECM: "engine (ECM)",
    TCM: "transmission (TCM)",
}
# 11-bit fallbacks, in case some Pilot answers there (none seen yet): 7E8 engine, 7E9 transmission.
MODULES_11BIT = {0x7E8: "engine (ECM)", 0x7E9: "transmission (TCM)"}


def module_name(addr):
    return MODULES.get(addr) or MODULES_11BIT.get(addr) or f"module {addr:02X}"


# Torque Pro equation letters: A is the first data byte after the echoed "62 xx xx", Z the 26th,
# AA the 27th, and so on.
def torque_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


CANDIDATES = [
    {
        "name": "ATF_TEMP",
        "label": "ATF F (candidate)",
        "request": bytes.fromhex("222201"),
        "module": TCM,
        "byte": "AA",
        "decode": lambda x: round((x - 40) * 9 / 5 + 32, 1),  # Torque equation AA-40 (deg C) -> deg F
        "source": "Piloteers owners ('09 LX, 2014s); vcmtuner.com Torque list (2022 Odyssey)",
        "seen": "2011 Pilot: TCM (1D) answers with 36 data bytes; ECM (11) refuses (7F 22 31). "
                "AA read 140 -> 145 F over 10 minutes of idle, warming as expected",
    },
    {
        "name": "VCM_CYLINDERS",
        "label": "VCM cylinders (candidate)",
        "request": bytes.fromhex("222615"),
        "module": ECM,
        "byte": "AY",
        "decode": lambda x: x,
        "source": "vcmtuner.com Torque list (2022 Odyssey); Piloteers post citing a Ridgeline list",
        "seen": "2011 Pilot: ECM (11) answers with 54 data bytes; TCM refuses. AY read 6 at idle "
                "(VCM never cuts at idle, so expected). Not yet seen on a cruise",
    },
]
BY_NAME = {c["name"]: c for c in CANDIDATES}


def payload(reply_payload, request):
    """Data bytes after the positive response '62 <id hi> <id lo>', or None."""
    want = bytes([0x62]) + request[1:3]
    if reply_payload and reply_payload[:3] == want:
        return reply_payload[3:]
    return None


def value(cand, reply_payload):
    data = payload(reply_payload, cand["request"])
    i = torque_index(cand["byte"])
    if data is None or i >= len(data):
        return None
    return cand["decode"](data[i])
