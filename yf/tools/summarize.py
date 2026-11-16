"""Summarize a yf_scan CSV: warm-up, charging, fuel trims by driving condition, and every noise marker.

    python3 yf/tools/summarize.py yf/logs/yf_XXXX.csv [marker_window_seconds]

The trim thresholds printed are common rules of thumb, not Honda specs.
"""

import csv
import statistics as st
import sys

path = sys.argv[1]
WIN = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

rows = list(csv.DictReader(open(path, encoding="utf-8")))
if not rows:
    sys.exit("empty log")


def num(r, k):
    try:
        return float(r[k])
    except (KeyError, ValueError, TypeError):
        return None


def col(k, rs=rows):
    return [v for v in (num(r, k) for r in rs) if v is not None]


def stats(vals):
    return f"mean {st.mean(vals):6.1f}  min {min(vals):6.1f}  max {max(vals):6.1f}" if vals else "no data"


t = col("elapsed_s")
print(f"{path}\n{len(rows)} rows over {t[-1]:.0f} s ({len(rows) / max(t[-1], 1):.2f} rows/s)")

# warm-up
ect = [(num(r, "elapsed_s"), num(r, "coolant F")) for r in rows if num(r, "coolant F") is not None]
if ect:
    warm = next((s for s, v in ect if v >= 176), None)
    print(f"\nCoolant: start {ect[0][1]:.0f} F, end {ect[-1][1]:.0f} F, max {max(v for _, v in ect):.0f} F; "
          + (f"reached 176 F (80 C) at {warm / 60:.1f} min" if warm is not None else "never reached 176 F"))

v = col("volts")
if v:
    print(f"Charging voltage: {stats(v)}  (rule of thumb: ~13.5-14.8 V running)")

# trims by condition
moving = lambda r: (num(r, "mph") or 0)
bands = [
    ("idle (0 mph)", lambda r: moving(r) == 0 and (num(r, "rpm") or 0) > 400),
    ("city (1-40 mph)", lambda r: 0 < moving(r) <= 40),
    ("highway (>40 mph)", lambda r: moving(r) > 40),
]
print("\nFuel trims, % (sustained total STFT+LTFT beyond about +/-10 % is worth a look):")
for name, sel in bands:
    rs = [r for r in rows if sel(r)]
    if not rs:
        print(f"  {name}: no rows")
        continue
    parts = []
    for b in ("1", "2"):
        s, l = col(f"STFT{b} %", rs), col(f"LTFT{b} %", rs)
        if s and l:
            parts.append(f"bank {b}: STFT {st.mean(s):+5.1f}  LTFT {st.mean(l):+5.1f}  total {st.mean(s) + st.mean(l):+5.1f}")
    rpm, load = col("rpm", rs), col("load %", rs)
    print(f"  {name}, {len(rs)} rows, rpm {st.mean(rpm):.0f}, load {st.mean(load):.0f} %" if rpm and load else f"  {name}, {len(rs)} rows")
    for p in parts:
        print(f"      {p}")
l1, l2 = col("LTFT1 %"), col("LTFT2 %")
if l1 and l2:
    print(f"  LTFT bank difference (1 minus 2): {st.mean(l1) - st.mean(l2):+.1f} %  (one bank off alone points at that bank: "
          "vacuum leak, injector, A/F sensor)")

# optional Honda candidate columns
for k in ("ATF F (candidate)", "VCM cylinders (candidate)"):
    vals = col(k)
    if vals:
        print(f"\n{k}: {stats(vals)}  (unconfirmed decode, cross-check before trusting)")

# markers
marks = [(i, r) for i, r in enumerate(rows) if r.get("marker")]
print(f"\nMarkers: {len(marks) or 'none'}")
for i, r in marks:
    t0 = num(r, "elapsed_s")
    win = [x for x in rows if abs((num(x, "elapsed_s") or 0) - t0) <= WIN]
    def rng(k):
        vals = col(k, win)
        return f"{min(vals):.0f}-{max(vals):.0f}" if vals else "-"
    rpm, mph = num(r, "rpm"), num(r, "mph")
    ratio = f"{rpm / mph:.0f} rpm/mph" if rpm and mph and mph > 3 else ("stopped" if mph == 0 else "-")
    print(f"  '{r['marker']}' at {t0:.0f} s ({t0 / 60:.1f} min): rpm {rpm:.0f}, {mph:.0f} mph, load {num(r, 'load %') or 0:.0f} %, "
          f"throttle {num(r, 'throttle %') or 0:.0f} %, {ratio}")
    print(f"      +/-{WIN:.0f} s: rpm {rng('rpm')}, mph {rng('mph')}, load {rng('load %')} %"
          + (f", VCM {rng('VCM cylinders (candidate)')}" if col("VCM cylinders (candidate)", win) else ""))
if len(marks) > 1:
    print("  Compare markers: same rpm at different speeds -> engine side; same speed at different rpm (gears) -> "
          "wheels/driveline; only at high load -> mounts/exhaust; only with fewer VCM cylinders -> VCM.")
