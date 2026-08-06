#!/usr/bin/env python3
"""Offline checks for the jitter instrument. No camera, no hand, no daemon.

    python3 test_measure_jitter.py

The instrument is going to decide the filter's parameters, so the things
asserted here are the ones that would make its numbers lie: a gate that
counts travel wrong, a gain table that has drifted from the mapping it
claims to read, and above all a filter whose behaviour depends on the
frame rate it happened to be measured at.
"""
import math
import sys

import measure_jitter as mj
import hand_mapping as hm

AXES = mj.AXES
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def const(v, n=200):
    return [[v] * 6 for _ in range(n)]


def noisy(sd_per_axis, n=600, seed=3):
    import random
    r = random.Random(seed)
    return [[1500 + r.gauss(0, sd_per_axis[i]) for i in range(6)]
            for _ in range(n)]


# ---- gates ---------------------------------------------------------------

still = mj.gate_coupled(const(1500), 12)
check("a signal that never changes sends once and travels nowhere",
      still["sent"] == 1 and still["travel"] == 0.0
      and still["finger_travel"] == 0.0)

check("deadband=0 releases every frame",
      mj.gate_coupled(noisy([5] * 6), 0)["sent"] == 600)

# The whole reason for per-axis gating, and the operator's actual complaint:
# today one noisy axis drags five quiet ones along with it, so the fingers
# twitch because the thumb is noisy. Total travel barely notices - it is
# dominated by the loud axis either way - which is why the claim has to be
# made against finger travel, the part that is visibly wrong.
one_loud = noisy([1, 1, 1, 1, 1, 20])
coupled = mj.gate_coupled(one_loud, 12)
per_axis = mj.gate_per_axis(one_loud, {a: 12 for a in AXES})
check("coupled gating makes quiet fingers move when only the thumb is noisy",
      coupled["finger_travel"] > 500,
      f"{coupled['finger_travel']:.0f} counts of finger travel from thumb noise")
check("per-axis gating removes almost all of it",
      per_axis["finger_travel"] < coupled["finger_travel"] * 0.05,
      f"{coupled['finger_travel']:.0f} -> {per_axis['finger_travel']:.0f}")
check("while leaving the genuinely noisy axis alone to be filtered",
      per_axis["axis_travel"]["thumb_rot"]
      > 0.8 * coupled["axis_travel"]["thumb_rot"],
      "a gate is not a filter; it must not pretend to fix real noise")
check("and it blames the axis that is actually noisy",
      max(coupled["blame"], key=coupled["blame"].get) == "thumb_rot")
check("travel adds up to the per-axis breakdown",
      abs(coupled["travel"] - sum(coupled["axis_travel"].values())) < 1e-6)

check("a larger deadband never increases travel",
      all(mj.gate_coupled(one_loud, d)["travel"]
          >= mj.gate_coupled(one_loud, d + 4)["travel"]
          for d in (0, 4, 8, 12, 16, 24)))


# ---- gain table ----------------------------------------------------------
#
# These numbers set the per-axis thresholds, so they must be read from the
# mapping rather than copied out of it. Recomputing them here from the same
# constants is the check that measure_jitter did not freeze a stale window.

g = mj.gains()
check("finger gain matches the curl window",
      abs(g["pinky"] - (hm.T_MAX - hm.T_MIN) / (hm.CURL_CLOSED - hm.CURL_OPEN)) < 1e-9,
      f"{g['pinky']:.2f} counts/deg")
check("thumb_rot gain matches the opposition window",
      abs(g["thumb_rot"] - (hm.T_MAX - hm.ROT_MIN) / (hm.OPP_MAX - hm.OPP_MIN)) < 1e-9,
      f"{g['thumb_rot']:.2f} counts/deg")
check("every axis has a gain", set(g) == set(AXES))


# ---- filters -------------------------------------------------------------

def step_response(filt, fps, tau_or_fc, n_seconds=2.0):
    """Time for a filter to cover 63% of a unit step, in seconds."""
    n = int(fps * n_seconds)
    dts = [1.0 / fps] * n
    y = filt([1000.0] * n, dts, tau_or_fc)
    for k, v in enumerate(y):
        if v >= 632.0:
            return k / fps
    return float("inf")


# A step starting from the filter's own first sample is instantaneous, so
# prime it with a zero and then step. This is the property the EMA in
# teleop_app.py does not have, and the reason it is being replaced.
def primed(filt, fps, arg, n_seconds=2.0):
    n = int(fps * n_seconds)
    dts = [1.0 / fps] * n
    xs = [0.0] + [1000.0] * (n - 1)
    y = filt(xs, dts, arg)
    for k, v in enumerate(y):
        if v >= 632.0:
            return k / fps
    return float("inf")


t30 = primed(mj.ema, 30.0, 0.2)
t10 = primed(mj.ema, 10.0, 0.2)
t4 = primed(mj.ema, 4.0, 0.2)
check("ema reaches 63% at tau regardless of frame rate",
      all(abs(t - 0.2) <= 1.0 / 4.0 for t in (t30, t10, t4)),
      f"30 FPS {t30:.3f}s  10 FPS {t10:.3f}s  4 FPS {t4:.3f}s")


def fixed_alpha(series, dts, w):
    """What teleop_app.py:317-318 does today, for contrast."""
    out, y = [], None
    for x in series:
        y = x if y is None else w * y + (1 - w) * x
        out.append(y)
    return out


f30 = primed(fixed_alpha, 30.0, 0.65)
f4 = primed(fixed_alpha, 4.0, 0.65)
check("the fixed-alpha ema does not: its time constant tracks the frame rate",
      f4 > f30 * 4,
      f"30 FPS {f30:.3f}s vs 4 FPS {f4:.3f}s - same setting, {f4/max(f30,1e-9):.0f}x")

still = noisy([5] * 6)
raw_travel = mj.gate_coupled(still, 12)["travel"]


def filtered_travel(cols):
    return mj.gate_coupled([[cols[i][k] for i in range(6)]
                            for k in range(len(cols[0]))], 12)["travel"]


dts = [1 / 30.0] * len(still)
check("ema removes most of the travel a still hand commands",
      filtered_travel([mj.ema([r[i] for r in still], dts, 0.1)
                       for i in range(6)]) < raw_travel * 0.25)
check("one-euro does too",
      filtered_travel([mj.one_euro([r[i] for r in still], dts, 1.0, 0.005)
                       for i in range(6)]) < raw_travel * 0.25)

# beta=0 is the degenerate case the docstring claims: a plain low-pass.
flat = [[1500.0] * 6 for _ in range(100)]
check("one-euro holds a constant signal exactly",
      all(abs(v - 1500.0) < 1e-6
          for v in mj.one_euro([r[0] for r in flat], [1 / 30.0] * 100, 1.0, 0.02)))

# A filter must not invent motion the input never had.
check("no filter overshoots the input range",
      max(mj.one_euro([r[0] for r in still], dts, 1.0, 0.02))
      <= max(r[0] for r in still) + 1e-6)


print()
if fails:
    print(f"{len(fails)} check(s) failed: {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
