"""Replay recorded thumb angles through the old and the proposed window.

Nothing is written. The angles in the CSVs are geometry, independent of
any window, so a window can be tried on them after the fact.

thumb_probe.csv (14:50) is the honest test: the proposed window was
derived from thumb_calib.csv (15:41) and has never seen the earlier
recording.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
T_MIN, T_MAX, ROT_MIN = 300, 2000, 300

OLD = {"THUMB_OPEN": 16.7, "THUMB_CLOSED": 172.8,
       "OPP_MIN": 15.5, "OPP_MAX": 112.2}
NEW = {"THUMB_OPEN": 18.0, "THUMB_CLOSED": 79.7,
       "OPP_MIN": 6.0, "OPP_MAX": 133.0}


def scale(value, lo, hi, out_lo, out_hi):
    n = (value - lo) / (hi - lo)
    return int(out_lo + max(0.0, min(1.0, n)) * (out_hi - out_lo))


def targets(flex, opp, w):
    return (scale(flex, w["THUMB_CLOSED"], w["THUMB_OPEN"], T_MIN, T_MAX),
            scale(opp, w["OPP_MAX"], w["OPP_MIN"], ROT_MIN, T_MAX))


def load(name, model=None):
    rows = []
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return rows
    for r in csv.DictReader(open(path)):
        if r["trust"] != "1":
            continue
        if model and r.get("model") != model:
            continue
        rows.append((float(r["flexion"]), float(r["opposition"])))
    return rows


def report(title, rows):
    if not rows:
        print("%s: no data" % title)
        return
    print("\n%s  (%d trusted frames)" % (title, len(rows)))
    print("  %-8s %-22s %-22s" % ("", "tgt_bend", "tgt_rot"))
    print("  %-8s %-22s %-22s" % ("window", "range      used   stuck",
                                  "range      used   stuck"))
    for tag, w in (("old", OLD), ("new", NEW)):
        b, o = [], []
        for flex, opp in rows:
            tb, tr = targets(flex, opp, w)
            b.append(tb)
            o.append(tr)
        def col(v, floor):
            used = 100.0 * (max(v) - min(v)) / (T_MAX - T_MIN)
            stuck = 100.0 * sum(1 for x in v if x in (floor, T_MAX)) / len(v)
            return "%4d..%4d  %5.1f%%  %5.1f%%" % (min(v), max(v), used, stuck)
        print("  %-8s %-22s %-22s" % (tag, col(b, T_MIN), col(o, ROT_MIN)))


print("old window:", OLD)
print("new window:", NEW)
print("\n'used' = fraction of the 300..2000 travel the targets span")
print("'stuck' = fraction of frames pinned at either end (dead zone)")

report("HELD OUT -- thumb_probe.csv, full model (new window never saw it)",
       load("thumb_probe.csv", model="full"))
report("HELD OUT -- thumb_probe.csv, lite model",
       load("thumb_probe.csv", model="lite"))
report("FITTED -- thumb_calib.csv (the window came from this one)",
       load("thumb_calib.csv"))
