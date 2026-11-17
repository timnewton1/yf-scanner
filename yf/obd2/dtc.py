"""Trouble codes: decoding Mode 03/07/0A replies, and short descriptions for the codes most likely
on a 2009-2015 Pilot.

The descriptions are the generic SAE J2012 wording (P0xxx) or the common Honda meaning (P3400 etc.).
For anything not listed, look the code up for a 2011 Pilot specifically: P1xxx and many
P2xxx/P3xxx codes are manufacturer-defined and mean different things on different makes.
"""

LETTERS = "PCBU"

# Mode -> (request byte, label)
MODES = {
    "stored": (0x03, "stored (confirmed; these turn the light on)"),
    "pending": (0x07, "pending (seen once, not confirmed yet)"),
    "permanent": (0x0A, "permanent (clear only after the car itself confirms the fix)"),
}

DESCRIPTIONS = {
    "P0101": "MAF sensor circuit range/performance",
    "P0102": "MAF sensor circuit low",
    "P0103": "MAF sensor circuit high",
    "P0106": "MAP sensor circuit range/performance",
    "P0107": "MAP sensor circuit low",
    "P0108": "MAP sensor circuit high",
    "P0111": "intake air temperature sensor range/performance",
    "P0112": "intake air temperature sensor circuit low",
    "P0113": "intake air temperature sensor circuit high",
    "P0116": "engine coolant temperature sensor range/performance",
    "P0117": "engine coolant temperature sensor circuit low",
    "P0118": "engine coolant temperature sensor circuit high",
    "P0122": "throttle position sensor A circuit low",
    "P0123": "throttle position sensor A circuit high",
    "P0128": "coolant temperature below thermostat regulating temperature (often a stuck-open thermostat)",
    "P0134": "O2 sensor no activity, bank 1 sensor 1",
    "P0135": "O2 / A-F sensor heater circuit, bank 1 sensor 1",
    "P0137": "O2 sensor circuit low voltage, bank 1 sensor 2",
    "P0138": "O2 sensor circuit high voltage, bank 1 sensor 2",
    "P0141": "O2 sensor heater circuit, bank 1 sensor 2",
    "P0155": "O2 / A-F sensor heater circuit, bank 2 sensor 1",
    "P0157": "O2 sensor circuit low voltage, bank 2 sensor 2",
    "P0158": "O2 sensor circuit high voltage, bank 2 sensor 2",
    "P0161": "O2 sensor heater circuit, bank 2 sensor 2",
    "P0171": "system too lean, bank 1",
    "P0172": "system too rich, bank 1",
    "P0174": "system too lean, bank 2",
    "P0175": "system too rich, bank 2",
    "P0300": "random / multiple cylinder misfire",
    "P0301": "cylinder 1 misfire",
    "P0302": "cylinder 2 misfire",
    "P0303": "cylinder 3 misfire",
    "P0304": "cylinder 4 misfire",
    "P0305": "cylinder 5 misfire",
    "P0306": "cylinder 6 misfire",
    "P0325": "knock sensor 1 circuit",
    "P0335": "crankshaft position sensor A circuit",
    "P0340": "camshaft position sensor A circuit",
    "P0420": "catalyst efficiency below threshold, bank 1",
    "P0430": "catalyst efficiency below threshold, bank 2",
    "P0441": "EVAP system incorrect purge flow",
    "P0442": "EVAP system small leak",
    "P0455": "EVAP system large leak",
    "P0456": "EVAP system very small leak",
    "P0457": "EVAP leak: fuel cap loose or off",
    "P0500": "vehicle speed sensor",
    "P0506": "idle speed lower than expected",
    "P0507": "idle speed higher than expected",
    "P0562": "system voltage low",
    "P0563": "system voltage high",
    "P0700": "transmission control system (the transmission module has its own code; read module 1D)",
    "P0705": "transmission range sensor circuit",
    "P0715": "input / turbine speed sensor circuit",
    "P0720": "output speed sensor circuit",
    "P0730": "incorrect gear ratio",
    "P0740": "torque converter clutch circuit",
    "P0741": "torque converter clutch performance or stuck off",
    "P0780": "shift error",
    # Manufacturer-defined, common Honda meanings
    "P2646": "rocker arm (VTEC) oil pressure system performance or stuck off, bank 1 (Honda)",
    "P2647": "rocker arm (VTEC) oil pressure system stuck on, bank 1 (Honda)",
    "P3400": "cylinder deactivation (VCM) system stuck off, bank 1 (Honda)",
    "P3497": "cylinder deactivation (VCM) system, bank 2 (Honda)",
}


def code(two):
    """Two bytes -> 'P0301'."""
    a, b = two[0], two[1]
    return f"{LETTERS[a >> 6]}{(a >> 4) & 0x3}{a & 0xF:X}{b:02X}"


def parse(payload):
    """A 43/47/4A reply -> ['P0301', ...]. On CAN the first data byte is the count."""
    if not payload or len(payload) < 2:
        return []
    n = payload[1]
    body = payload[2:2 + 2 * n]
    out = [code(body[i:i + 2]) for i in range(0, len(body) - 1, 2)]
    return [c for c in out if c != "P0000"]


def describe(c):
    return DESCRIPTIONS.get(c, "not in yf's list; look it up for a 2011 Pilot (P1xxx and many P2/P3 codes are Honda's own)")
