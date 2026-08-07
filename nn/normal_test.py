"""Why is opposition unusable? Two suspects, tested on recorded landmarks.

Suspect 1 -- the palm normal. hand_mapping builds it from a single cross
product of two vectors that both start at the wrist, so the normal is
decided entirely by the z of three points, where a single camera is
weakest. Tested against a least-squares plane through five points and
against a time-smoothed normal.

Suspect 2 -- atan2 wraparound. opposition is an angle with no branch cut
handling. A pose sitting near +-180 produces readings at both ends, and
an ordinary median or percentile over that is meaningless. Tested by
redoing the same statistics on the circle.

A held pose should give one answer. Whichever change makes the spread
inside a pose smallest, and the gap between poses largest, wins.

    ../venv/bin/python3 normal_test.py
"""
import csv
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = {1: "A1", 2: "A2", 3: "A3", 4: "A4", 5: "B1", 6: "B2", 7: "B3", 8: "B4"}
PALM = (0, 5, 9, 13, 17)
ALPHA = 0.15          # normal smoothing; palm orientation is a slow signal


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


def frame_3pt(p):
    """Exactly _palm_frame: e1 uses the UNFLIPPED n, then n flips."""
    a = p[5] - p[0]
    n = np.cross(a, p[17] - p[0])
    e1 = np.cross(a, n)
    return unit(a), unit(-n), unit(e1)          # HANDEDNESS == "Right"


def normal_5fit(p, ref):
    """Least-squares plane through wrist + four MCPs; sign follows ref."""
    q = p[list(PALM)]
    q = q - q.mean(axis=0)
    n = np.linalg.svd(q)[2][-1]
    return unit(n if np.dot(n, ref) >= 0 else -n)


def opp_with(a, n, e1, t):
    y, x = float(np.dot(t, n)), float(np.dot(t, e1))
    return math.degrees(math.atan2(y, x)) if math.hypot(y, x) > 1e-6 else None


def swap_normal(a, n_new, e1_ref):
    """Rebuild an orthonormal frame around a new normal, e1 sign preserved."""
    a2 = unit(a - np.dot(a, n_new) * n_new)
    if a2 is None:
        return None, None
    e1 = np.cross(a2, -n_new)                   # mirrors _palm_frame's order
    if np.dot(e1, e1_ref) < 0:
        e1 = -e1
    return a2, unit(e1)


def circ_stats(deg):
    """Median and IQR taken on the circle, so a pose near +-180 is not
    reported as if it spanned the whole range."""
    r = np.radians(deg)
    c = math.atan2(float(np.mean(np.sin(r))), float(np.mean(np.cos(r))))
    d = [(math.degrees(math.atan2(math.sin(x - c), math.cos(x - c))))
         for x in r]
    return (math.degrees(c) + pct(d, 50),
            pct(d, 75) - pct(d, 25))


rows = list(csv.DictReader(open(os.path.join(HERE,
                                             "thumb_steps_landmarks.csv"))))
print("%d landmark rows" % len(rows))
VARIANTS = ("3pt", "5fit", "ema")

for model in ("lite", "full"):
    print("\n\n" + "#" * 66)
    print("########  %s" % model)
    print("#" * 66)
    per = {v: {} for v in VARIANTS}
    ema_n, last_step = None, None
    for r in rows:
        if r["model"] != model or r["trust"] != "1":
            continue
        s = int(r["step"])
        if s != last_step:
            ema_n, last_step = None, s     # each pose starts its own filter
        p = np.array([[float(r["x%d" % j]), float(r["y%d" % j]),
                       float(r["z%d" % j])] for j in range(21)])
        a, n3, e1 = frame_3pt(p)
        t = unit(p[2] - p[1])
        if a is None or n3 is None or e1 is None or t is None:
            continue
        ema_n = n3 if ema_n is None else unit(ALPHA * n3 + (1 - ALPHA) * ema_n)
        for name, n in (("3pt", n3), ("5fit", normal_5fit(p, n3)),
                        ("ema", ema_n)):
            if name == "3pt":
                o = opp_with(a, n, e1, t)
            else:
                a2, e2 = swap_normal(a, n, e1)
                o = None if a2 is None else opp_with(a2, n, e2, t)
            if o is not None:
                per[name].setdefault(s, []).append(o)

    steps = sorted(per["3pt"])
    if not steps:
        print("  no trusted frames")
        continue

    for label, circ in (("PLAIN percentiles (what the code effectively does)",
                         False), ("CIRCULAR percentiles (wrap-aware)", True)):
        print("\n  --- %s ---" % label)
        print("  spread inside each held pose  (p75-p25; a held pose should"
              " be one number)")
        print("  %-6s %s" % ("", " ".join("%7s" % TAG[s] for s in steps)))
        best = {}
        for name in VARIANTS:
            sp = []
            for s in steps:
                v = per[name][s]
                sp.append(circ_stats(v)[1] if circ
                          else pct(v, 75) - pct(v, 25))
            best[name] = sum(sp) / len(sp)
            print("  %-6s %s   mean %6.1f"
                  % (name, " ".join("%7.1f" % x for x in sp), best[name]))

        print("\n  median per pose   (block B should climb or fall steadily)")
        for name in VARIANTS:
            med = [circ_stats(per[name][s])[0] if circ else pct(per[name][s], 50)
                   for s in steps]
            b = [m for s, m in zip(steps, med) if s >= 5]
            mono = "yes" if b and (all(y > x for x, y in zip(b, b[1:]))
                                   or all(y < x for x, y in zip(b, b[1:]))) \
                else "NO"
            print("  %-6s %s   B monotonic: %s"
                  % (name, " ".join("%7.1f" % m for m in med), mono))

        print("\n  separation = (spread of the four B medians) /"
              " (mean spread inside a B pose);  >2 is usable")
        for name in VARIANTS:
            bs = [s for s in steps if s >= 5]
            med, sp = [], []
            for s in bs:
                v = per[name][s]
                m, i = circ_stats(v) if circ else (pct(v, 50),
                                                   pct(v, 75) - pct(v, 25))
                med.append(m)
                sp.append(i)
            w = sum(sp) / len(sp)
            print("  %-6s between %6.1f   within %6.1f   score %5.2f"
                  % (name, max(med) - min(med), w,
                     (max(med) - min(med)) / w if w > 1e-6 else float("inf")))
