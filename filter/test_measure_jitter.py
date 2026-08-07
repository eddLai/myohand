#!/usr/bin/env python3
"""Offline checks for the jitter instrument. No camera, no hand, no daemon.

    python3 test_measure_jitter.py

The instrument is going to decide the filter's parameters, so the things
asserted here are the ones that would make its numbers lie: a gate that
counts travel wrong, a gain table that has drifted from the mapping it
claims to read, and above all a filter whose behaviour depends on the
frame rate it happened to be measured at.
"""
import argparse
import contextlib
import csv
import io
import math
import os
import random
import sys
import tempfile

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


# ---- amplitude is not path length ----------------------------------------
#
# travel counts every wiggle; p2p_f measures the swing. Reporting only the
# first lets a filter claim a win it did not get, so the instrument reports
# both and these are the two ways they disagree.

def cols_of(rows):
    return [[r[i] for r in rows] for i in range(6)]


flat_fingers = [[1500, 1500, 1500, 1500, 1500, 1500 + 400 * (k % 2)]
                for k in range(50)]
check("p2p_f ignores the thumb axes, like finger_travel does",
      mj.p2p_fingers(cols_of(flat_fingers)) == 0,
      "a thumb swinging 400 counts is not a finger moving")

# Drift plus noise on one finger: filtering kills the noise, and the drift
# is real hand movement that must survive it. Travel collapses; the swing
# the operator sees does not. Reporting travel alone would read that as a
# filter that fixed the problem.
_r = random.Random(5)
drift = [[1500 + k + _r.gauss(0, 5)] + [1500] * 5 for k in range(120)]
d_raw = cols_of(drift)
d_filt = [mj.ema(c, [1 / 30.0] * 120, 0.3) for c in d_raw]
gd = mj.gains()
t_raw = mj.gate_per_axis(drift, {a: 1.5 * gd[a] for a in AXES})["travel"]
t_filt = mj.gate_per_axis(mj.as_rows(d_filt, 120),
                          {a: 1.5 * gd[a] for a in AXES})["travel"]
p_raw, p_filt = mj.p2p_fingers(d_raw), mj.p2p_fingers(d_filt)
check("travel and p2p_f disagree: a filter cuts the path, not the swing",
      t_filt < 0.6 * t_raw and p_filt > 0.7 * p_raw,
      f"travel {t_raw:.0f} -> {t_filt:.0f}, p2p_f {p_raw:.0f} -> {p_filt:.0f}")


# ---- filters and gates do not compose -------------------------------------
#
# The reason the sweep crosses them instead of scoring filters through the
# old gate and gates on raw data. Filtering a quiet axis does not stop the
# coupled gate from releasing it: that is decided by a different axis.

one_loud = noisy([1, 1, 1, 1, 1, 20])
dts_l = [1 / 30.0] * len(one_loud)
filt = [mj.one_euro([r[i] for r in one_loud], dts_l, 1.0, 0.005)
        for i in range(6)]
frows = mj.as_rows(filt, len(one_loud))
g = mj.gains()
dbs = {a: 1.5 * g[a] for a in AXES}
fc = mj.gate_coupled(frows, 12)
fp = mj.gate_per_axis(frows, dbs)
check("the same filtered signal scores differently under the two gates",
      fc["finger_travel"] > 10 * max(fp["finger_travel"], 1e-9),
      f"coupled {fc['finger_travel']:.0f} vs per-axis "
      f"{fp['finger_travel']:.0f} counts of finger travel")


# ---- telemetry column handling --------------------------------------------

def write_csv(path, n=120, with_tele=False):
    r = random.Random(11)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(mj.FIELDS)
        for k in range(n):
            tgt = [1500 + r.gauss(0, 4) for _ in AXES]
            row = ([f"{k / 30.0:.4f}", 1, 1, ""]
                   + [f"{v:.1f}" for v in tgt]
                   + [f"{r.gauss(80, 1):.3f}" for _ in mj.FINGERS]
                   + [f"{r.gauss(60, 1):.3f}", f"{r.gauss(45, 1):.3f}"])
            if with_tele:
                # one gap, so the "carry telemetry" count has to be real
                row += ([""] * 12 if k == 5 else
                        [f"{1500 + r.gauss(0, 2):.0f}" for _ in AXES]
                        + [f"{abs(r.gauss(0, 3)):.0f}" for _ in AXES])
            else:
                row += [""] * 12
            w.writerow(row)


# ---- settling: the head of a recording is not noise ------------------------
#
# The real still.csv of 2026-08-07 opened with the operator arriving and
# MediaPipe converging, which read as sd 17.3 counts on the index axis
# against 6.3 for the rest of the run. Tuning against the inflated figure
# buys an over-smoothed filter and pays for it in latency, so the
# instrument has to show the transient and be able to drop it.

_r2 = random.Random(9)
loud_then_quiet = ([[1500 + _r2.gauss(0, 40) for _ in AXES] for _ in range(60)]
                   + [[1500 + _r2.gauss(0, 4) for _ in AXES] for _ in range(240)])
ts_lq = [k / 30.0 for k in range(300)]
blocks = mj.settling(ts_lq, loud_then_quiet)
check("settling shows a loud opening block against a quiet closing one",
      max(blocks[0][1]) > 3 * max(blocks[-1][1]),
      f"first {max(blocks[0][1]):.0f} vs last {max(blocks[-1][1]):.0f} counts sd")
check("settling covers every frame in five blocks",
      len(blocks) == 5 and blocks[-1][0][1] == ts_lq[-1])

with tempfile.TemporaryDirectory() as d:
    plain = os.path.join(d, "plain.csv")
    tele = os.path.join(d, "tele.csv")
    write_csv(plain, with_tele=False)
    write_csv(tele, with_tele=True)

    rows_p, _, _, _, _ = mj.load(plain)
    rows_t, _, _, _, _ = mj.load(tele)
    check("a recording without --telemetry reports no telemetry",
          mj.telemetry(rows_p) is None)
    got = mj.telemetry(rows_t)
    check("a recording with --telemetry parses both ANGLEACT and current",
          got is not None and len(got) == 2 and len(got[0][0]) == 6)
    check("and drops the frames whose telemetry is blank rather than faking",
          len(got[0]) == len(rows_t) - 1,
          f"{len(got[0])} of {len(rows_t)} frames carried it")

    # The whole print path had never been executed before this test existed.
    for name, path, want in (("without telemetry", plain, "filters x gates"),
                             ("with telemetry", tele, "nothing drove it")):
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                mj.analyse(argparse.Namespace(csv=path, deg=1.5, skip=0.0))
            ok, why = want in buf.getvalue(), ""
        except Exception as e:                 # noqa: BLE001
            ok, why = False, f"{type(e).__name__}: {e}"
        check(f"analyse runs end to end {name}", ok, why)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mj.analyse(argparse.Namespace(csv=plain, deg=1.5, skip=0.0))
    check("the idle-baseline section is absent when nothing recorded it",
          "nothing drove it" not in buf.getvalue())

    kept_all, _, _, _, dropped0 = mj.load(plain, 0.0)
    kept_1s, _, _, _, dropped1 = mj.load(plain, 1.0)
    check("--skip drops exactly the frames inside the window",
          dropped0 == 0 and dropped1 == 30
          and len(kept_all) - len(kept_1s) == dropped1,
          f"1.0 s of 30 FPS dropped {dropped1} frames")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            mj.load(plain, 999.0)
        ok = False
    except SystemExit:
        ok = True
    check("--skip longer than the recording exits with a message", ok)


print()
if fails:
    print(f"{len(fails)} check(s) failed: {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
