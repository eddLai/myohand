"""Where does the thumb go behind the palm, and how fast does it leak after?

The linear fit failed its held-out set for a reason worth keeping: between the
first two sweep positions the thumb travels 53 degrees of opposition and the
flexion reading does not move at all.  Occlusion is not gradual.  The thumb is
in plain sight until it passes behind the palm, and only then does the net
start drawing one that is not there.

So the correction needs an onset as well as a slope, and two parameters is one
more than two held poses can support.  This scans the onset instead of fitting
it, and scores each candidate on both recordings at once: the held poses have
to agree with each other, and the sweep positions have to stop climbing.  A
value that only satisfies one of them is a value that has fitted noise.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "camera"))
import hand_mapping as hm  # noqa: E402


class L(object):
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


def lms(r):
    return [L(float(r["x%d" % j]), float(r["y%d" % j]), float(r["z%d" % j]))
            for j in range(21)]


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def group(path, key, only=None):
    out = {}
    for r in csv.DictReader(open(path)):
        if r.get("trust") != "1" or r.get("model") != "lite":
            continue
        if only and r.get(only[0]) != only[1]:
            continue
        out.setdefault(r[key], []).append(lms(r))
    return out


def raw(frames):
    hm.OPP_LEAK = 0.0
    f = [hm.thumb_features(l, hm.HANDEDNESS) for l in frames]
    return med([x["flexion"] for x in f]), med([x["opposition"] for x in f])


poses = group(os.path.join(HERE, "thumb_calib_ui_landmarks.csv"), "pose")
steps = group(os.path.join(HERE, "thumb_steps_landmarks.csv"), "step",
              ("block", "B"))

held = {p: raw(poses[p]) for p in ("P3", "P4", "P5", "P6")}
sweep = [(s,) + raw(steps[s]) for s in sorted(steps)]

print("held poses (today)      flexion  opposition")
for p in ("P5", "P6", "P3", "P4"):
    print("  %-20s %8.1f %11.1f" % (p, held[p][0], held[p][1]))
print("\nsweep positions (2026-08-07, held out)")
for s, f, o in sweep:
    print("  step %-15s %8.1f %11.1f" % (s, f, o))


def corrected(flex, opp, onset, k):
    return max(0.0, flex - k * max(0.0, opp - onset))


print("\n%-7s %7s   %-24s %-24s" % ("onset", "slope",
                                    "held: P5 vs P6 gap", "sweep: spread"))
best = None
for onset in range(0, 101, 10):
    d_opp = held["P6"][1] - max(onset, held["P5"][1])
    if d_opp <= 5:
        continue
    k = (held["P6"][0] - held["P5"][0]) / d_opp
    gap = abs(corrected(*held["P6"], onset=onset, k=k)
              - corrected(*held["P5"], onset=onset, k=k))
    vals = [corrected(f, o, onset, k) for _, f, o in sweep]
    spread = max(vals) - min(vals)
    sep = (corrected(*held["P4"], onset=onset, k=k)
           - corrected(*held["P3"], onset=onset, k=k))
    score = gap + spread
    mark = ""
    if best is None or score < best[0]:
        best, mark = (score, onset, k), "  <-"
    print("%-7d %7.3f   gap %6.1f              spread %6.1f   bend kept %5.1f%s"
          % (onset, k, gap, spread, sep, mark))

print("\nraw spread across the sweep, uncorrected: %.1f"
      % (max(f for _, f, _ in sweep) - min(f for _, f, _ in sweep)))
print("best on both at once: onset %d, slope %.3f" % (best[1], best[2]))
