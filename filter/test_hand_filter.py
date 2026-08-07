#!/usr/bin/env python3
"""Offline checks for the shipped filter. No camera, no hand, no daemon.

    python3 test_hand_filter.py

measure_jitter's tests cover the primitives as maths. What is asserted
here is the part that only exists once the filter is streaming: that the
batch view and the live view are the same code, that a still hand produces
no traffic, that one noisy axis cannot release five quiet ones, that a
dropped frame cannot un-smooth the filter, and that a hold stays a hold.
"""
import math
import random
import sys

import hand_filter as hf

AXES = hf.AXES
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


#: gains close to camera/hand_mapping's, but stated here so these checks do
#: not move when somebody recalibrates. The real thresholds come from
#: camera_gains(); that link is asserted separately below.
G = {"pinky": 6.04, "ring": 6.04, "middle": 6.04, "index": 6.04,
     "thumb_bend": 8.59, "thumb_rot": 7.80}


def stream(filt, frames, dt=1 / 30.0, t0=0.0):
    """Feed frames one at a time; return (outputs, how often it said send)."""
    outs, sends = [], 0
    for k, f in enumerate(frames):
        out = filt.update(f, t0 + k * dt)
        outs.append(out)
        sends += 1 if filt.changed else 0
    return outs, sends


# ---- the batch view and the live view must be one implementation ---------
#
# The whole reason the primitives live in this module and measure_jitter
# imports them. If these ever disagree, every parameter in the sweep was
# chosen for code that is not the code running on the hand.

r = random.Random(7)
series = [1500 + r.gauss(0, 12) for _ in range(300)]
dts = [1 / 30.0] * 300

batch = hf.one_euro(series, dts, hf.MINCUTOFF, hf.BETA, hf.DCUTOFF)
live = hf._OneEuro(hf.MINCUTOFF, hf.BETA, hf.DCUTOFF)
streamed = [live.update(x, dt) for x, dt in zip(series, dts)]
check("one_euro batch == one_euro streamed, exactly",
      all(abs(a - b) < 1e-12 for a, b in zip(batch, streamed)))

batch_e = hf.ema(series, dts, 0.2)
live_e = hf._Ema(0.2)
check("ema batch == ema streamed, exactly",
      all(abs(a - live_e.update(x, dt)) < 1e-12
          for a, x, dt in zip(batch_e, series, dts)))

# measure_jitter must be scoring these very functions.
sys.path.insert(0, ".")
import measure_jitter as mj  # noqa: E402

check("measure_jitter scores the shipped filter, not a copy",
      mj.one_euro is hf.one_euro and mj.ema is hf.ema)
check("and the shipped thresholds are the swept thresholds",
      mj.gains() == hf.camera_gains())


# ---- a still hand must produce no traffic --------------------------------

#
# Scored against the unfiltered baseline rather than an absolute count: the
# synthetic noise here is white, and the real thing is 65-83% below 1.8 Hz,
# so an absolute threshold would be asserting a property of this generator.
# The claim that survives both is the reduction.

still = [[1500 + r.gauss(0, 12) for _ in AXES] for _ in range(600)]
filt = hf.HandFilter(G)
outs, sends = stream(filt, still)


def travel(outs):
    return sum(abs(outs[k][i] - outs[k - 1][i])
               for k in range(1, len(outs)) for i in range(6))


raw_filt = hf.HandFilter(G, mincutoff=1e6, beta=0.0, deg=0.0)
raw_outs, raw_sends = stream(raw_filt, still)
check("a still hand commands almost none of the travel it would raw",
      travel(outs) < 0.05 * travel(raw_outs),
      f"{travel(outs):.0f} vs {travel(raw_outs):.0f} counts")
check("and it goes quiet instead of sending every frame",
      sends < 0.03 * len(still),
      f"{sends} sends in {len(still)} frames, against {raw_sends} raw")

flat = [[1500.0] * 6 for _ in range(200)]
filt = hf.HandFilter(G)
outs, sends = stream(filt, flat)
check("a perfectly constant input sends exactly once", sends == 1)
check("and holds the value it was given",
      all(abs(v - 1500.0) < 1e-6 for v in outs[-1]))


# ---- one noisy axis must not release five quiet ones ---------------------
#
# The operator's actual complaint. thumb_rot measures 2.15 deg of noise
# against 1.0-1.8 for the rest and released the old max-over-axes gate on
# 64% of frames, taking the four fingers with it.

loud_thumb = [[1500, 1500, 1500, 1500, 1500, 1500 + r.gauss(0, 40)]
              for _ in range(400)]
filt = hf.HandFilter(G)
outs, _ = stream(filt, loud_thumb)
finger_travel = sum(abs(outs[k][i] - outs[k - 1][i])
                    for k in range(1, len(outs)) for i in range(4))
check("a noisy thumb commands no finger travel at all",
      finger_travel < 1e-6, f"{finger_travel:.1f} counts reached the fingers")


# ---- a dropped frame must not un-smooth the filter -----------------------
#
# Both alphas approach 1 as dt grows, so the frame after a gap is the least
# filtered one - exactly where MediaPipe is least sure. Measured on
# dropout.csv: a 634 ms gap passed 96% of the jump in a single frame.

def jump_passed(dt_gap, dt_max):
    f = hf.HandFilter(G, dt_max=dt_max)
    t = 0.0
    for _ in range(120):                       # settle at 1500
        f.update([1500.0] * 6, t)
        t += 1 / 30.0
    before = f._out[0]
    t += dt_gap
    f.update([1800.0] * 6, t)                  # a 300-count jump across the gap
    return (f._out[0] - before) / (1800.0 - before)


normal = jump_passed(1 / 30.0, hf.DT_MAX)
gaps = [0.122, 0.232, 0.470, 0.634]          # the four in dropout.csv
clamped = [jump_passed(g, hf.DT_MAX) for g in gaps]
loose = [jump_passed(g, 999.0) for g in gaps]

check("the dt clamp makes gap size irrelevant",
      all(abs(c - normal) < 1e-6 for c in clamped),
      f"ordinary {normal*100:.1f}%, gaps {[f'{c*100:.1f}%' for c in clamped]}")
check("without it a gap passes progressively more, growing with the gap",
      loose == sorted(loose) and loose[-1] > normal * 1.3,
      f"{normal*100:.1f}% -> {[f'{v*100:.1f}%' for v in loose]}")

# The same probe on the EMA reproduces the number this defence was built
# from, which is what says the probe is measuring the right thing.
e = hf._Ema(0.2)
for _ in range(120):
    e.update(1500.0, 1 / 30.0)
b = e.y
e.update(1800.0, 0.634)
check("the probe reproduces the 96% measured on dropout.csv for ema tau=0.2",
      (e.y - b) / 300.0 > 0.9, f"{(e.y-b)/3:.0f}% - one-euro at the shipped "
      f"mincutoff is inherently far more gap-resistant")

check("DT_MAX is about twice a 30 FPS frame, as measured",
      0.05 < hf.DT_MAX < 0.10, f"{hf.DT_MAX*1000:.0f} ms")

# Not bounded by this module: a fast real move is supposed to pass, so the
# filter cannot also be the thing that limits step size. 300 counts in goes
# to ~113 out in a single tick even on an ordinary frame. The hard bound is
# a per-axis rate limit, and it belongs in hand_fw with the other mechanical
# guards - see filter/README.
check("a single frame can still command a large step, by design",
      normal > 0.3, f"{normal*100:.0f}% of a 300-count jump in one tick "
      f"- the hard bound is hand_fw's rate limit, not this filter")


# ---- holds ---------------------------------------------------------------
#
# HOLD means the source could not measure this axis. Feeding the filter a
# substitute would let the hold decay into the estimate and quietly become
# a command, which is the bug the current teleop hack is one step away from.

filt = hf.HandFilter(G)
stream(filt, [[1500.0] * 6 for _ in range(60)])
held_before = filt._out[5]
outs, _ = stream(filt, [[1500, 1500, 1500, 1500, 1500, hf.HOLD]
                        for _ in range(60)], t0=10.0)
check("a held axis does not move while it is held",
      abs(filt._out[5] - held_before) < 1e-9)
check("and its filter state is untouched, not fed a substitute",
      abs(filt._filters[5].x_hat - held_before) < 1e-9)

filt = hf.HandFilter(G)
out = filt.update([hf.HOLD] * 6, 0.0)
check("a first frame that is all holds sends nothing",
      filt.changed is False)


# ---- the parameters are the measured ones -------------------------------
#
# Not a style check. beta=0 at this mincutoff measured 450 ms of lag
# against 69 ms with it, so a well-meaning "simplify to a plain low-pass"
# would cost a factor of six and look like a cleanup.

check("mincutoff is the measured one, far below any textbook default",
      hf.MINCUTOFF == 0.05)
check("beta is non-zero: at this mincutoff it is load-bearing, not decoration",
      hf.BETA > 0)
check("dcutoff is raised above the customary 1.0",
      hf.DCUTOFF > 1.0, f"{hf.DCUTOFF} - worth 27 ms of lag, measured")

db = hf.deadbands(G, 1.5)
check("deadbands are one tolerance in degrees, scaled per axis",
      abs(db["pinky"] - 1.5 * 6.04) < 1e-9
      and abs(db["thumb_bend"] - 1.5 * 8.59) < 1e-9,
      f"pinky {db['pinky']:.0f} counts, thumb_bend {db['thumb_bend']:.0f}")
check("so one number is not three different physical tolerances",
      db["thumb_bend"] > db["pinky"])

# The slider retunes this live and nothing else, which is the point: it is
# monotonic, so turning it cannot land the filter in a strange regime.
filt = hf.HandFilter(G, deg=1.5)
wide, narrow = [], []
for deg, into in ((4.0, wide), (0.5, narrow)):
    f = hf.HandFilter(G)
    f.set_deadband_deg(deg)
    _, sends = stream(f, still)
    into.append(sends)
check("a wider deadband sends strictly less than a narrow one",
      wide[0] < narrow[0], f"4.0 deg: {wide[0]} sends, 0.5 deg: {narrow[0]}")
check("set_deadband_deg leaves the one-euro state alone",
      filt.set_deadband_deg(2.0) is None
      and filt._filters[0].x_hat is None)


# ---- interface contract --------------------------------------------------

filt = hf.HandFilter(G)
try:
    filt.update([1500] * 5, 0.0)
    ok = False
except ValueError:
    ok = True
check("a wrong-length pose is rejected rather than silently padded", ok)

filt = hf.HandFilter(G)
out = filt.update([1500.0] * 6, 0.0)
check("update always returns six values", len(out) == 6)
filt.reset()
check("reset clears the state", filt._out is None and filt.changed is False)


print()
if fails:
    print(f"{len(fails)} check(s) failed: {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
