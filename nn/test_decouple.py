"""Does the measured correction hold up away from where it was measured?

The table comes from one recording: a straight thumb held at eight sweep
angles. Everything here is an attempt to catch it failing somewhere else.

The independent set is thumb_steps, recorded for a different purpose before
any of this existed. Nothing in it shaped the table, and it happens to sample
a sweep angle inside the four degree window where the cliff hides - still flat
at 69.7, where the newer recording is flat at 66.4 and has jumped by 70.4 - so
it also says whether the cliff was put in the right place.

Two things could go wrong that flattening alone would not show. A correction
big enough to erase an invented bend is big enough to erase a real one, so the
genuinely curled pose has to keep its distance from the straight one. And an
empty table has to be inert, since every profile in existence has one.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "camera"))
import hand_mapping as hm  # noqa: E402

# nn/fit_table.py, lite, from sweep_probe.csv. Zero is pinned to one degree
# short of the first angle that showed the jump: the recording says there is
# no leak below the cliff, and ramping into it would invent one.
TABLE = [[0.0, 0.0], [66.4, 0.0], [69.4, 0.0],
         [70.4, 28.1], [79.7, 34.0], [94.1, 44.0]]

FAIL = []


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
    return float("nan") if not n else (
        xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def spread(xs):
    xs = sorted(xs)
    return float("nan") if len(xs) < 5 else (
        xs[int(len(xs) * 0.9)] - xs[int(len(xs) * 0.1)])


def group(path, key, only=None):
    out = {}
    for r in csv.DictReader(open(path)):
        if r.get("trust") != "1" or r.get("model") != "lite":
            continue
        if only and r.get(only[0]) != only[1]:
            continue
        out.setdefault(r[key], []).append(lms(r))
    return out


def flex(frames):
    return [hm.thumb_features(f, hm.HANDEDNESS)["flexion"] for f in frames]


def opp(frames):
    return [hm.thumb_features(f, hm.HANDEDNESS)["opposition"] for f in frames]


def check(name, cond, detail):
    print("  %-52s %s" % (name, "ok" if cond else "FAIL"))
    print("      %s" % detail)
    if not cond:
        FAIL.append(name)


CAL = os.path.join(HERE, "thumb_calib_ui_landmarks.csv")
STEPS = os.path.join(HERE, "thumb_steps_landmarks.csv")
for p in (CAL, STEPS):
    if not os.path.exists(p):
        sys.exit("missing %s" % p)
poses = group(CAL, "pose")
steps = group(STEPS, "step", ("block", "B"))

print("=== 1. the same straight thumb, pointing two ways ===")
hm.OPP_LEAK_TABLE = []
r5, r6 = med(flex(poses["P5"])), med(flex(poses["P6"]))
hm.OPP_LEAK_TABLE = TABLE
c5, c6 = med(flex(poses["P5"])), med(flex(poses["P6"]))
check("P5 and P6 agree", abs(c6 - c5) < 5.0,
      "%.1f vs %.1f, gap %.1f (was %.1f)" % (c5, c6, abs(c6 - c5), abs(r6 - r5)))

print("\n=== 2. a genuine bend keeps its distance ===")
hm.OPP_LEAK_TABLE = []
raw_sep = med(flex(poses["P4"])) - med(flex(poses["P3"]))
hm.OPP_LEAK_TABLE = TABLE
sep = med(flex(poses["P4"])) - med(flex(poses["P3"]))
check("straight to curled survives", sep > 0.9 * raw_sep,
      "%.1f corrected vs %.1f raw (%.0f%% kept)"
      % (sep, raw_sep, 100.0 * sep / raw_sep))

print("\n=== 3. independent set: thumb_steps ===")
hm.OPP_LEAK_TABLE = []
raw = [(s, med(flex(steps[s])), med(opp(steps[s]))) for s in sorted(steps)]
noise = max(spread(flex(steps[s])) for s in sorted(steps))
hm.OPP_LEAK_TABLE = TABLE
cor = [(s, med(flex(steps[s]))) for s in sorted(steps)]
print("  %-6s %11s %10s %11s" % ("step", "opposition", "raw", "corrected"))
for (s, rf, ro), (_, cf) in zip(raw, cor):
    print("  %-6s %11.1f %10.1f %11.1f" % (s, ro, rf, cf))
rr = max(r[1] for r in raw) - min(r[1] for r in raw)
cr = max(c[1] for c in cor) - min(c[1] for c in cor)
check("a straight thumb reads flat while it sweeps", cr < noise,
      "spread %.1f, was %.1f, and one held angle scatters %.1f" % (cr, rr, noise))

print("\n=== 4. an empty table is inert ===")
hm.OPP_LEAK_TABLE = []
check("every profile in existence is unaffected", med(flex(poses["P5"])) == r5,
      "P5 reads %.1f either way" % r5)

print("\n%s" % ("all ok" if not FAIL else "FAILED: " + ", ".join(FAIL)))
sys.exit(1 if FAIL else 0)
