"""Honda enhanced reads (Mode 22, ReadDataByIdentifier). Read-only; nothing here changes the car.

Every entry is a CANDIDATE found for other Hondas. None is confirmed on a 2009-2015 Pilot yet.
Run `yf_scan.py --honda-probe` to see the raw bytes the car sends back, and confirm the
decoded value against something you trust (ATF: thermocouple on the cooler line) before
believing it. See docs/honda-enhanced.md for sources and what's been tried.
"""

import obd
from obd import OBDCommand
from obd.protocols import ECU

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
        "request": b"222201",
        "byte": "AA",
        "decode": lambda x: round((x - 40) * 9 / 5 + 32, 1),  # Torque equation AA-40 (deg C) -> deg F
        "source": "vcmtuner.com Torque PID list (2022 Odyssey); Piloteers 3rd-gen owners",
    },
    {
        "name": "VCM_CYLINDERS",
        "label": "VCM cylinders (candidate)",
        "request": b"222615",
        "byte": "AY",
        "decode": lambda x: x,
        "source": "vcmtuner.com Torque PID list (2022 Odyssey); Piloteers post citing a Ridgeline list",
    },
]

# 7E0 is the engine computer. 7E1 is the usual address for a separate transmission computer;
# whether this Pilot has one is unknown, so the probe asks both.
HEADERS = [b"7E0", b"7E1"]


def raw_decoder(messages):
    return [list(m.data) for m in messages]


def command(cand, header=b"7E0"):
    return OBDCommand(f"HONDA_{cand['name']}_{header.decode()}", cand["label"], cand["request"], 0,
                      raw_decoder, ECU.ALL, False, header)


def payload(frames, request):
    """Data bytes after the positive response '62 <id hi> <id lo>', from the first ECU that answered."""
    want = [0x62] + list(bytes.fromhex(request.decode()[2:]))
    for data in frames or []:
        if data[:3] == want:
            return data[3:]
    return None


def value(cand, frames):
    data = payload(frames, cand["request"])
    i = torque_index(cand["byte"])
    if data is None or i >= len(data):
        return None
    return cand["decode"](data[i])


def query_raw(conn, cmd):
    r = conn.query(cmd, force=True)
    return None if r.is_null() else r.value


def probe(conn):
    print("Honda enhanced candidates (read-only Mode 22). Raw bytes shown so the decode can be checked.")
    for cand in CANDIDATES:
        for header in HEADERS:
            frames = query_raw(conn, command(cand, header))
            tag = f"{cand['name']} @ {header.decode()}"
            if not frames:
                print(f"  {tag}: no answer")
                continue
            data = payload(frames, cand["request"])
            hexed = [" ".join(f"{b:02X}" for b in f) for f in frames]
            print(f"  {tag}: {len(frames)} frame(s): {' | '.join(hexed)}")
            if data is None:
                print("    not a positive response (7F = refused or not supported)")
                continue
            v = value(cand, frames)
            i = torque_index(cand["byte"])
            print(f"    {len(data)} data bytes; byte {cand['byte']} (index {i}) -> "
                  f"{'missing' if v is None else v} {cand['label']}")
