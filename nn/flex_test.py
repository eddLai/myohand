"""Five ways to measure thumb bend, replayed on the recorded landmarks.

The 2026-08-07 step test showed the channel that fails is bend, not
opposition: pressing the thumb flat on the palm reads LOWER than bending
it two thirds. A window cannot fix a measurement that reverses. The
question is whether a different formula, on the same frames, stays
monotonic all the way to a closed thumb.

Judged on three things:
  monotonic   A1 < A2 < A3 < A4, because those poses are ordered by how
              closed the thumb is
  A3->A4 gap  measured in units of the spread inside a pose; the closed
              end is where the current formula gives up
  leakage     block B sweeps the thumb across the palm without bending
              it, so a bend measure should barely move there

Reads thumb_steps_landmarks.csv. Writes nothing, changes nothing.

    ../venv/bin/python3 flex_test.py
"""
import csv
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = {1: "A1 straight", 2: "A2 third", 3: "A3 two thirds", 4: "A4 flat",
       5: "B1 open", 6: "B2 index", 7: "B3 ring", 8: "B4 pinky"}
PALM = (0, 5, 9, 13, 17)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


def joint_angle(p, a, b, c):
    """hand_mapping._joint_angle: bend at b, 0 when the bones are straight."""
    u, v = p[a] - p[b], p[c] - p[b]
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    d = max(-1.0, min(1.0, float(np.dot(u, v)) / (nu * nv)))
    return 180.0 - math.degrees(math.acos(d))


def variants(p):
    """Every candidate, all oriented so LARGER means MORE CLOSED."""
    mcp = joint_angle(p, 1, 2, 3)
    ip = joint_angle(p, 2, 3, 4)
    chain = (np.linalg.norm(p[2] - p[1]) + np.linalg.norm(p[3] - p[2])
             + np.linalg.norm(p[4] - p[3]))
    span = np.linalg.norm(p[4] - p[1])
    hand = np.linalg.norm(p[9] - p[0])
    palm_c = p[list(PALM)].mean(axis=0)
    return {
        "current  mcp+ip": mcp + ip,
        "mcp only": mcp,
        "ip only": ip,
        # how much of the thumb's own length is lost to curling: 0 straight,
        # towards 1 fully curled. Needs no palm frame, so opposition cannot
        # leak into it through the normal.
        "curl ratio": 100.0 * (1.0 - span / chain) if chain > 1e-9 else 0.0,
        # tip pulled in toward the palm, scaled by hand size and inverted so
        # larger still means more closed
        "tip->palm": (100.0 * (1.0 - np.linalg.norm(p[4] - palm_c) / hand)
                      if hand > 1e-9 else 0.0),
    }


rows = list(csv.DictReader(open(os.path.join(HERE,
                                             "thumb_steps_landmarks.csv"))))
print("%d landmark rows\n" % len(rows))
NAMES = list(variants(np.zeros((21, 3))))

for model in ("lite", "full"):
    print("\n" + "=" * 78)
    print("  %s" % model)
    print("=" * 78)
    per = {n: {} for n in NAMES}
    for r in rows:
        if r["model"] != model or r["trust"] != "1":
            continue
        p = np.array([[float(r["x%d" % j]), float(r["y%d" % j]),
                       float(r["z%d" % j])] for j in range(21)])
        for n, v in variants(p).items():
            per[n].setdefault(int(r["step"]), []).append(v)

    A = [s for s in (1, 2, 3, 4) if per[NAMES[0]].get(s)]
    B = [s for s in (5, 6, 7, 8) if per[NAMES[0]].get(s)]
    if len(A) < 4:
        print("  block A incomplete")
        continue

    for n in NAMES:
        med = [pct(per[n][s], 50) for s in A]
        iqr = [pct(per[n][s], 75) - pct(per[n][s], 25) for s in A]
        mono = all(b > a for a, b in zip(med, med[1:]))
        w = sum(iqr) / len(iqr)
        gap = (med[3] - med[2]) / w if w > 1e-9 else float("inf")
        leak = 0.0
        if len(B) >= 2:
            bm = [pct(per[n][s], 50) for s in B]
            leak = max(bm) - min(bm)
        rng = med[3] - med[0]

        print("\n  --- %s ---" % n)
        print("    %-14s %s"
              % ("median", "  ".join("%7.1f" % m for m in med)))
        print("    %-14s %s"
              % ("spread(p75-25)", "  ".join("%7.1f" % v for v in iqr)))
        print("    %-14s %s" % ("", "  ".join("%7s" % TAG[s].split()[0]
                                              for s in A)))
        print("    monotonic A1<A2<A3<A4 : %s" % ("YES" if mono else "NO"))
        print("    A3 -> A4 gap          : %+6.1f  (%.1f x the in-pose spread)"
              % (med[3] - med[2], gap))
        print("    full range A1 -> A4   : %6.1f   (in-pose spread %.1f)"
              % (rng, w))
        if len(B) >= 2:
            print("    leakage during sweep  : %6.1f   (%.0f%% of the range)"
                  % (leak, 100.0 * leak / rng if abs(rng) > 1e-9 else 0))

    print("\n  what would count as a win: monotonic YES, a clearly positive")
    print("  A3->A4 gap, a large range next to the in-pose spread, and")
    print("  leakage small compared with the range.")
