"""Summarize a scan.py CSV: its run info (the # lines), a timeline, warm-up, charging, fuel trims by driving condition (coasting
apart), how the long-term trims moved, and every noise marker.

    python3 summarize.py logs/yf_XXXX.csv [marker_window_seconds] [timeline_step_seconds]

summarize(path) returns the same text, for tui.py and the phone app. The trim thresholds printed are
common rules of thumb, not Honda specs.
"""

import csv
import statistics as st
import sys

LINK = ("timeout", "link lost", "reconnected")  # rows the logger writes about the link, not noise markers


def num(r, k):
    try:
        return float(r[k])
    except (KeyError, ValueError, TypeError):
        return None


def stats(vals):
    return f"mean {st.mean(vals):6.1f}  min {min(vals):6.1f}  max {max(vals):6.1f}" if vals else "no data"


def is_link(m):
    return m.startswith(LINK)


def summarize(path, WIN=3.0, STEP=30.0, name=None):
    """The summary of one log, as text. name: what to call the file in the first line (default: path).
    Raises ValueError for an empty log."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    info = [ln[1:].strip() for ln in lines if ln.startswith("#")]  # run info, from logger.CsvLog
    rows = list(csv.DictReader(ln for ln in lines if not ln.startswith("#")))
    if not rows:
        raise ValueError("empty log")
    out = []

    say = out.append

    def col(k, rs=None):
        return [v for v in (num(r, k) for r in (rows if rs is None else rs)) if v is not None]

    path = name or path
    t = col("elapsed_s")
    data_rows = [r for r in rows if not is_link(r.get("marker", ""))]
    say(f"{path}\n{len(data_rows)} rows over {t[-1]:.0f} s ({len(data_rows) / max(t[-1], 1):.2f} rows/s)")
    if info:
        say("Run info:\n" + "\n".join(f"  {ln}" for ln in info))
    else:
        say("Run info: none (a log from before logs recorded it)")
    link = [r for r in rows if is_link(r.get("marker", ""))]
    if link:
        lost = sum(r["marker"].startswith("link lost") for r in link)
        slow = sum(r["marker"] == "timeout" for r in link)
        say(f"Link: {lost} drop(s), {slow} timeout(s). Gaps in the data at: "
              + ", ".join(f"{num(r, 'elapsed_s'):.0f} s" for r in link if r["marker"].startswith("link lost")))
    rows = data_rows

    # timeline: one row every STEP seconds, plus the last row; columns the log doesn't have are left out.
    # Each cell is the column's last value at or before that row: a quick log reads slow values only
    # every 10-60 s, so the row itself often has them blank.
    TL = [("rpm", "rpm", 5, "5.0f"), ("mph", "mph", 4, "4.0f"), ("load %", "load", 4, "4.0f"), ("throttle %", "thr", 4, "4.0f"),
          ("coolant F", "ect", 4, "4.0f"), ("STFT1 %", "st1", 6, "+6.1f"), ("LTFT1 %", "lt1", 6, "+6.1f"),
          ("STFT2 %", "st2", 6, "+6.1f"), ("LTFT2 %", "lt2", 6, "+6.1f"), ("volts", "V", 5, "5.1f")]
    TL = [c for c in TL if col(c[0])]
    say(f"\nTimeline, every {STEP:.0f} s:")
    say(f"{'t':>6} " + " ".join(f"{h:>{w}}" for _, h, w, _ in TL) + "  marker")
    next_t = 0.0
    last = {}
    for r in rows:
        for k, *_ in TL:
            if num(r, k) is not None:
                last[k] = num(r, k)
        tr = num(r, "elapsed_s")
        if tr is None or not (tr >= next_t or r is rows[-1]):
            continue
        next_t = tr + STEP
        cells = []
        for k, _, w, f in TL:
            x = last.get(k)
            cells.append(f"{x:{f}}" if x is not None else f"{'-':>{w}}")
        say(f"{tr:6.0f} " + " ".join(cells) + (f"  {r['marker']}" if r.get("marker") else ""))

    # warm-up
    ect = [(num(r, "elapsed_s"), num(r, "coolant F")) for r in rows if num(r, "coolant F") is not None]
    if ect:
        warm = next((s for s, v in ect if v >= 176), None)
        say(f"\nCoolant: start {ect[0][1]:.0f} F, end {ect[-1][1]:.0f} F, max {max(v for _, v in ect):.0f} F; "
              + (f"reached 176 F (80 C) at {warm / 60:.1f} min" if warm is not None else "never reached 176 F"))

    v = col("volts")
    if v:
        say(f"Charging voltage: {stats(v)}  (rule of thumb: ~13.5-14.8 V running)")

    # trims by condition. Coasting (moving, throttle closed) gets its own band so it doesn't blur city/highway.
    # Closed throttle is taken as within 2 points of the lowest throttle % seen at idle (or in the whole log if
    # it never idled), because the absolute throttle PID doesn't read 0 % when closed.
    moving = lambda r: (num(r, "mph") or 0)
    thr = col("throttle %", [r for r in rows if moving(r) == 0]) or col("throttle %")
    closed_at = min(thr) + 2 if thr else None
    coasting = lambda r: closed_at is not None and moving(r) > 5 and (num(r, "throttle %") or 100) <= closed_at
    bands = [
        ("idle (0 mph)", lambda r: moving(r) == 0 and (num(r, "rpm") or 0) > 400),
        ("city (1-40 mph, throttle open)", lambda r: 0 < moving(r) <= 40 and not coasting(r)),
        ("highway (>40 mph, throttle open)", lambda r: moving(r) > 40 and not coasting(r)),
    ]
    if closed_at is not None:
        bands.append((f"coasting (>5 mph, throttle <= {closed_at:.0f} %)", coasting))
    say("\nFuel trims, % (sustained total STFT+LTFT beyond about +/-10 % is worth a look; "
          "trims while coasting can swing hard if the PCM cuts fuel, so read that band on its own):")
    for name, sel in bands:
        rs = [r for r in rows if sel(r)]
        if not rs:
            say(f"  {name}: no rows")
            continue
        parts = []
        for b in ("1", "2"):
            s, l = col(f"STFT{b} %", rs), col(f"LTFT{b} %", rs)
            if s and l:
                parts.append(f"bank {b}: STFT {st.mean(s):+5.1f}  LTFT {st.mean(l):+5.1f}  total {st.mean(s) + st.mean(l):+5.1f}")
        rpm, load = col("rpm", rs), col("load %", rs)
        say(f"  {name}, {len(rs)} rows, rpm {st.mean(rpm):.0f}, load {st.mean(load):.0f} %" if rpm and load else f"  {name}, {len(rs)} rows")
        for p in parts:
            say(f"      {p}")
    l1, l2 = col("LTFT1 %"), col("LTFT2 %")
    if l1 and l2:
        say(f"  LTFT bank difference (1 minus 2): {st.mean(l1) - st.mean(l2):+.1f} %  (one bank off alone points at that bank: "
              "vacuum leak, injector, A/F sensor)")

    # how the long-term trims moved over the drive
    for b in ("1", "2"):
        pts = [(num(r, "elapsed_s"), num(r, f"LTFT{b} %")) for r in rows]
        pts = [(tt, x) for tt, x in pts if tt is not None and x is not None]
        if not pts:
            continue
        changes = [(tt, x) for (tt, x), (_, prev) in zip(pts[1:], pts) if x != prev]
        lo, hi = min(x for _, x in pts), max(x for _, x in pts)
        line = (f"LTFT{b}: start {pts[0][1]:+.1f} %, end {pts[-1][1]:+.1f} %, range {lo:+.1f} to {hi:+.1f} %, "
                f"{len(changes)} change{'s' if len(changes) != 1 else ''}")
        if changes:
            shown = ", ".join(f"{x:+.1f} at {tt:.0f} s" for tt, x in changes[:12])
            line += f": {shown}" + (f", ... {len(changes) - 12} more" if len(changes) > 12 else "")
        say(("\n" if b == "1" else "") + line)

    # optional Honda candidate columns
    for k in ("ATF F (candidate)", "VCM cylinders (candidate)"):
        vals = col(k)
        if vals:
            say(f"\n{k}: {stats(vals)}  (unconfirmed decode, cross-check before trusting)")
    vcm = [(num(r, "VCM cylinders (candidate)"), moving(r)) for r in rows if num(r, "VCM cylinders (candidate)") is not None]
    if vcm:
        cut = [mph for c, mph in vcm if c < 6]
        say(f"  rows with fewer than 6 cylinders: {len(cut)} of {len(vcm)}"
              + (f", at {min(cut):.0f}-{max(cut):.0f} mph" if cut else
                 " (VCM cuts cylinders on a light steady cruise; none seen yet, or the decode is wrong)"))

    # markers
    marks = [(i, r) for i, r in enumerate(rows) if r.get("marker")]
    say(f"\nMarkers: {len(marks) or 'none'}")
    for i, r in marks:
        t0 = num(r, "elapsed_s")
        win = [x for x in rows if abs((num(x, "elapsed_s") or 0) - t0) <= WIN]
        def rng(k):
            vals = col(k, win)
            return f"{min(vals):.0f}-{max(vals):.0f}" if vals else "-"
        rpm, mph = num(r, "rpm"), num(r, "mph")
        ratio = f"{rpm / mph:.0f} rpm/mph" if rpm and mph and mph > 3 else ("stopped" if mph == 0 else "-")
        say(f"  '{r['marker']}' at {t0:.0f} s ({t0 / 60:.1f} min): rpm {rpm:.0f}, {mph:.0f} mph, load {num(r, 'load %') or 0:.0f} %, "
              f"throttle {num(r, 'throttle %') or 0:.0f} %, {ratio}")
        say(f"      +/-{WIN:.0f} s: rpm {rng('rpm')}, mph {rng('mph')}, load {rng('load %')} %"
              + (f", VCM {rng('VCM cylinders (candidate)')}" if col("VCM cylinders (candidate)", win) else ""))
    if len(marks) > 1:
        say("  Compare markers: same rpm at different speeds -> engine side; same speed at different rpm (gears) -> "
              "wheels/driveline; only at high load -> mounts/exhaust; only with fewer VCM cylinders -> VCM.")

    return "\n".join(out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.exit(__doc__)
    try:
        text = summarize(argv[0], float(argv[1]) if len(argv) > 1 else 3.0, float(argv[2]) if len(argv) > 2 else 30.0)
    except (OSError, ValueError) as e:
        sys.exit(str(e))
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
