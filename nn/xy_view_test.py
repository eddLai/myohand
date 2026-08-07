"""Does an image-plane proximity measure survive the hand turning?

Recorded frames showed the thumb tip 5% of a hand-length from the pinky
MCP in x and y -- touching, as the operator saw on screen -- while the
same pair read 66% apart in 3D. Dropping z recovers the signal that the
depth estimate destroys.

The reason the project moved off distances in the first place was
viewpoint: test_mapping measures joint angles wandering 0 target units
over 45 views while the old distance-ratio mapping wandered 1700. So the
question is not whether xy proximity sees a closing thumb -- it plainly
does -- but whether what it gains on occlusion it gives back on rotation.

Same harness, synthetic hand, 45 viewpoints. For each candidate:

  signal   how far apart the four thumb postures sit
  drift    how much one posture moves as the hand turns
  ratio    signal / drift; under about 2 the feature cannot be trusted
           to mean the same thing from a different angle

Synthetic data only. No camera, no files written.

    ../venv/bin/python3 xy_view_test.py
"""
import contextlib
import io
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):   # the module self-tests on import
    import test_mapping as tm  # noqa: E402

PALM = (0, 5, 9, 13, 17)
CURLS = (10.0, 30.0, 50.0, 75.0)      # straight -> pressed toward the palm


def d_xy(lm, a, b):
    return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)


def d_3d(lm, a, b):
    return math.dist((lm[a].x, lm[a].y, lm[a].z), (lm[b].x, lm[b].y, lm[b].z))


def palm_area_xy(lm):
    """Shoelace over the projected palm; shrinks as the hand turns away,
    which is exactly what a scale factor has to track."""
    p = [(lm[i].x, lm[i].y) for i in PALM]
    s = sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
            for i in range(len(p)))
    return abs(s) / 2.0


def features(lm):
    hand_xy = d_xy(lm, 0, 9)
    area = palm_area_xy(lm)
    tf = hm.thumb_features(lm, "Right")
    return {
        "flexion (3D angles, current)": tf["flexion"],
        "tip-pinky 3D / hand": 100.0 * d_3d(lm, 4, 17) / max(d_3d(lm, 0, 9), 1e-9),
        "tip-pinky XY / hand-XY": 100.0 * d_xy(lm, 4, 17) / max(hand_xy, 1e-9),
        "tip-pinky XY / sqrt(area)": 100.0 * d_xy(lm, 4, 17) / max(math.sqrt(area), 1e-9),
        "tip-palmC XY / hand-XY": 100.0 * math.hypot(
            lm[4].x - sum(lm[i].x for i in PALM) / 5.0,
            lm[4].y - sum(lm[i].y for i in PALM) / 5.0) / max(hand_xy, 1e-9),
    }


NAMES = list(features(tm.view(tm.build_hand(20.0), 0, 0, 0)))
print("%d viewpoints (yaw -40..40, pitch -30..30, roll -25..25)" % len(tm.VIEWS))
print("thumb curl swept %s deg with the fingers held open\n"
      % ", ".join("%g" % c for c in CURLS))

table = {n: {} for n in NAMES}
for c in CURLS:
    pts = tm.build_hand(5.0, thumb_curl_deg=c)
    for v in tm.VIEWS:
        f = features(tm.view(pts, *v))
        for n in NAMES:
            table[n].setdefault(c, []).append(f[n])

print("%-30s %28s %9s %9s %7s"
      % ("feature", "median at each thumb curl", "signal", "drift", "ratio"))
print("-" * 88)
best = []
for n in NAMES:
    med, dr = [], []
    for c in CURLS:
        v = sorted(table[n][c])
        med.append(v[len(v) // 2])
        dr.append(max(v) - min(v))
    signal = max(med) - min(med)
    drift = sum(dr) / len(dr)
    ratio = signal / drift if drift > 1e-9 else float("inf")
    best.append((ratio, n))
    print("%-30s %28s %9.1f %9.1f %7.2f"
          % (n, "  ".join("%6.1f" % m for m in med), signal, drift, ratio))

print("\n  signal = spread of the four medians (how well postures separate)")
print("  drift  = mean spread of one posture across the 45 views")
print("  ratio  = signal / drift.  A view-invariant measure has drift ~0")
print("           and an enormous ratio; under ~2 the feature says more")
print("           about where the hand is pointing than about the thumb.")
print("\n  ranking:")
for r, n in sorted(best, reverse=True):
    print("    %7.2f  %s" % (r, n))
