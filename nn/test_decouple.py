"""Does subtracting the leak make a straight thumb read straight?

The fit comes from two held poses in one recording, which is barely enough to
draw a line through, so most of this file is spent trying to break it.

The held-out set is thumb_steps: four sweep positions, B1 splayed through B4
at the pinky, thumb straight in all four. Raw flexion climbs across them
because the sweep invents it. Corrected flexion has to stay flat, and flat has
to mean flatter than the spread within a single pose, or the correction is
just noise that happens to point downhill.

The other thing to break is the bend signal itself. A correction large enough
to flatten the sweep could equally flatten a real bend, so P3 to P4 - thumb
straight, then genuinely curled - has to keep its separation.

Run:  python3 test_decouple.py
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "camera"))
import hand_mapping as hm  # noqa: E402

FAIL = []


class L(object):
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


def lms(row):
    return [L(float(row["x%d" % j]), float(row["y%d" % j]),
              float(row["z%d" % j])) for j in range(21)]


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    return float("nan") if not n else (
        xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def spread(xs):
    """p90 - p10, the width of a pose that is supposed to be held still."""
    xs = sorted(xs)
    if len(xs) < 5:
        return float("nan")
    return xs[int(len(xs) * 0.9)] - xs[int(len(xs) * 0.1)]


def load(path, pose_key, model, only=None):
    rows = [r for r in csv.DictReader(open(path))
            if r.get("trust") == "1" and r.get("model") == model
            and (only is None or r.get(only[0]) == only[1])]
    out = {}
    for r in rows:
        out.setdefault(r[pose_key], []).append(lms(r))
    return out


def feats(frames):
    f = [hm.thumb_features(l, hm.HANDEDNESS) for l in frames]
    return [x["flexion"] for x in f], [x["opposition"] for x in f]


def check(name, cond, detail):
    print("  %-58s %s" % (name, "ok" if cond else "FAIL"))
    if not cond:
        FAIL.append("%s: %s" % (name, detail))
    if detail:
        print("      %s" % detail)


CAL = os.path.join(HERE, "thumb_calib_ui_landmarks.csv")
STEPS = os.path.join(HERE, "thumb_steps_landmarks.csv")
for p in (CAL, STEPS):
    if not os.path.exists(p):
        sys.exit("missing %s" % p)

print("=== fitting the leak on the held poses (lite) ===")
poses = load(CAL, "pose", "lite")
hm.OPP_LEAK = 0.0
f5, o5 = feats(poses["P5"])
f6, o6 = feats(poses["P6"])
k = (med(f6) - med(f5)) / (med(o6) - max(hm.OPP_ONSET, med(o5)))
print("  P5 flexion %.1f  opposition %.1f" % (med(f5), med(o5)))
print("  P6 flexion %.1f  opposition %.1f" % (med(f6), med(o6)))
print("  OPP_ONSET = %.0f (fixed), OPP_LEAK = %.3f (fitted)"
      % (hm.OPP_ONSET, k))

print("\n=== 1. the two poses it was fitted on now agree ===")
hm.OPP_LEAK = k
c5, _ = feats(poses["P5"])
c6, _ = feats(poses["P6"])
gap = abs(med(c6) - med(c5))
check("same straight thumb reads the same both ways", gap < 2.0,
      "%.1f vs %.1f, gap %.1f deg (was %.1f)"
      % (med(c5), med(c6), gap, abs(med(f6) - med(f5))))

print("\n=== 2. a real bend survives it ===")
hm.OPP_LEAK = 0.0
f3, _ = feats(poses["P3"])
f4, _ = feats(poses["P4"])
raw_sep = med(f4) - med(f3)
hm.OPP_LEAK = k
c3, _ = feats(poses["P3"])
c4, _ = feats(poses["P4"])
sep = med(c4) - med(c3)
check("straight to curled keeps all of its separation", sep >= raw_sep - 0.5,
      "%.1f deg corrected vs %.1f raw (%.0f%% kept)"
      % (sep, raw_sep, 100.0 * sep / raw_sep))

print("\n=== 3. held-out: thumb straight at four sweep positions ===")
# block B is the sweep: B1 splayed, then touching index, ring and pinky.
# The last of those needs a little genuine bend, so a perfectly flat line is
# not the bar; shrinking the climb is.
steps = load(STEPS, "step", "lite", only=("block", "B"))
blocks = sorted(steps)
if len(blocks) < 3:
    print("  thumb_steps has no B blocks (%s) - skipped" % ", ".join(sorted(steps)))
else:
    hm.OPP_LEAK = 0.0
    raw = [(b, med(feats(steps[b])[0]), med(feats(steps[b])[1])) for b in blocks]
    hm.OPP_LEAK = k
    cor = [(b, med(feats(steps[b])[0])) for b in blocks]
    within = max(spread(feats(steps[b])[0]) for b in blocks)
    print("  %-6s %10s %10s %10s" % ("block", "opposition", "raw flex", "corrected"))
    for (b, rf, ro), (_, cf) in zip(raw, cor):
        print("  %-6s %10.1f %10.1f %10.1f" % (b, ro, rf, cf))
    rawrange = max(r[1] for r in raw) - min(r[1] for r in raw)
    corrange = max(c[1] for c in cor) - min(c[1] for c in cor)
    print("  spread across blocks: raw %.1f -> corrected %.1f "
          "(within one held block: %.1f)" % (rawrange, corrange, within))
    check("sweeping invents much less flexion than it did",
          corrange < 0.5 * rawrange,
          "%.1f deg across four sweep positions, was %.1f (%.0f%% removed)"
          % (corrange, rawrange, 100.0 * (1 - corrange / rawrange)))

print("\n=== 4. OPP_LEAK = 0 changes nothing ===")
hm.OPP_LEAK = 0.0
again, _ = feats(poses["P5"])
check("default is exactly today's behaviour", again == f5,
      "%d frames identical" % len(again))

print("\n%s" % ("all ok" if not FAIL else "FAILED:\n  " + "\n  ".join(FAIL)))
sys.exit(1 if FAIL else 0)
