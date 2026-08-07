#!/usr/bin/env python3
"""hand_filter - command shaping between a pose source and hand_fw.

Six-axis continuous target in, six-axis continuous target out. It sits
between `camera`/`nn` and `hand_fw` and does three things, in this order:

  1. one-euro low-pass, per axis, with dt clamped
  2. per-axis hysteresis, from one input tolerance scaled by each axis's gain
  3. hold semantics for axes the source could not measure

It reads no feedback. Nothing here looks at ANGLEACT, current or state, and
that is deliberate: the RH56F1 runs its own closed loop on its own MCU and
this layer must not become a second controller fighting it. A command that
is wrong here is late or coarse; a feedback loop that is wrong here winds
up against a grasp. See the filter/README for the argument.

THE FILTER PRIMITIVES LIVE HERE AND measure_jitter IMPORTS THEM. The
instrument that chooses the parameters has to be running the code that
ships, or the two drift and the numbers stop meaning anything - the same
reason measure_jitter.gains() reads the live mapping instead of copying it.

Parameters are measured, not guessed, and every default below carries the
recording it came from. They are also expected to be re-measured: every
number here is one operator, one camera, one light, one session, and no
hand in the loop. Re-record, re-sweep, and pass new values in - which is
why they are constructor arguments and not constants in the code.
"""
import math

#: axis order, shared with hand_safety.h and hand_sink
AXES = ("pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot")
FINGERS = ("pinky", "ring", "middle", "index")

#: hand_safety's HS_TGT_HOLD: leave this axis wherever it is
HOLD = -1

# ---- measured defaults ---------------------------------------------------
#
# From still.csv and moving.csv, .112, 2026-08-07, one operator at ~30 FPS,
# scored offline. The sweep that produced them is `measure_jitter analyse`.
#
# MINCUTOFF is far below any textbook one-euro default, and that is the
# finding rather than a typo. 65-83% of the still-hand noise variance sits
# below 1.8 Hz - it is slow wander, not high-frequency jitter, and a
# zero-phase 0.16 Hz filter still leaves 60% of the sd. So the baseline is
# nearly frozen (tau ~ 3.2 s) and BETA is what gives the axis back to the
# operator when they move. The two are load-bearing together: at this
# mincutoff, beta=0 measures 450 ms of lag against 69 ms with it. The usual
# defaults (fc=1.0, beta=0.007) were measured here and are actively bad.
#
# DCUTOFF is 4.0 rather than the customary 1.0 because the velocity estimate
# is what decides when beta opens the filter up, and a slow one makes it
# late: 96 ms of lag at dc=1.0 against 69 ms at dc=4.0 for the same jitter.
MINCUTOFF = 0.05      # Hz
BETA = 0.0005         # cutoff Hz per count/s of estimated speed
DCUTOFF = 4.0         # Hz, on the speed estimate itself

#: one tolerance in INPUT degrees; each axis's counts threshold is this
#: times its own gain, so the number means one physical thing on all six.
DEADBAND_DEG = 1.5

#: dt fed to the filter is clamped here. MediaPipe drops the hand and
#: re-acquires it, and both alphas approach 1 as dt grows, so the frame
#: after a gap is the least smoothed one - exactly where the pose estimate
#: is least reliable and the hand has moved unseen. Measured on dropout.csv:
#: a 634 ms gap passes 96% of the jump in one frame, a single-axis step of
#: 370 counts, 45% of full scale and 5x the p99 of an ordinary frame.
#: Clamping at ~2x the median frame time cuts that to 151 and costs 0.7
#: counts of mean lag on moving.csv. Resetting the filter on a gap was also
#: measured and is strictly worse - it is 100% pass-through by definition.
DT_MAX = 0.066        # seconds

#: a floor, not a policy. dt smaller than this is a duplicated frame or a
#: clock that went backwards, and dividing by it produces a vast speed.
DT_MIN = 1e-3


# ---- scalar cores --------------------------------------------------------
#
# Streaming, one axis each. The batch helpers below drive these, so an
# offline sweep and the live path cannot diverge.

class _Ema:
    """Exponential smoothing with a real time constant, in seconds.

    Kept because it is the honest baseline every proposal is scored
    against, and because teleop_app.py's fixed-per-frame weight is the
    thing being replaced: that one's time constant tracks the frame rate,
    about 77 ms at 30 FPS and 580 ms at the 3.9 FPS MediaPipe falls to
    while re-detecting on the KD240. Same slider, seven times the smoothing.
    """

    def __init__(self, tau):
        self.tau = tau
        self.y = None

    def reset(self):
        self.y = None

    def update(self, x, dt):
        if self.y is None:
            self.y = x
        else:
            a = 1.0 - math.exp(-dt / self.tau) if self.tau > 0 else 1.0
            self.y += a * (x - self.y)
        return self.y


class _OneEuro:
    """Cutoff rises with speed: heavy smoothing when still, light when moving.

    The trade-off a fixed alpha cannot escape - jitter when still or lag
    when moving, pick one - is what this filter was designed for, and noisy
    3D tracking driving an actuator is the case it was designed on.
    beta=0 degenerates to a plain low-pass at mincutoff.
    """

    def __init__(self, mincutoff=MINCUTOFF, beta=BETA, dcutoff=DCUTOFF):
        self.mincutoff, self.beta, self.dcutoff = mincutoff, beta, dcutoff
        self.x_hat = None
        self.dx_hat = 0.0

    def reset(self):
        self.x_hat = None
        self.dx_hat = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def update(self, x, dt):
        if self.x_hat is None:
            self.x_hat = x
            return self.x_hat
        dx = (x - self.x_hat) / dt
        self.dx_hat += self._alpha(self.dcutoff, dt) * (dx - self.dx_hat)
        cutoff = self.mincutoff + self.beta * abs(self.dx_hat)
        self.x_hat += self._alpha(cutoff, dt) * (x - self.x_hat)
        return self.x_hat


# ---- batch views, for offline sweeps -------------------------------------

def ema(series, dts, tau):
    """`_Ema` over a whole recording. Same code path as the live filter."""
    f = _Ema(tau)
    return [f.update(x, dt) for x, dt in zip(series, dts)]


def one_euro(series, dts, mincutoff=1.0, beta=0.0, dcutoff=1.0):
    """`_OneEuro` over a whole recording. Same code path as the live filter."""
    f = _OneEuro(mincutoff, beta, dcutoff)
    return [f.update(x, dt) for x, dt in zip(series, dts)]


# ---- thresholds ----------------------------------------------------------

def deadbands(gains, deg=DEADBAND_DEG):
    """Per-axis counts thresholds from one input tolerance in degrees.

    Each axis maps a different window of input degrees onto the same span
    of counts, so a threshold set in counts is a different physical
    tolerance on every axis: 12 counts is 2.0 deg on a finger and 1.4 on
    thumb-bend. Landmark noise happens in degrees, so the tolerance is set
    there and converted here. It also makes the parameter sayable - "I
    tolerate 1.5 degrees of hand tremor" is checkable and "deadband=12" is
    not.
    """
    return {a: deg * gains[a] for a in AXES}


def camera_gains():
    """Counts per input degree per axis, read from the live camera mapping.

    Imported lazily and by path so that a non-camera source - the EMG path
    is the other one - can use this module without dragging in MediaPipe's
    dependency tree. Callers with their own mapping should build their own
    gains dict and pass it; these numbers describe camera/hand_mapping.py
    and nothing else.
    """
    import os
    import sys
    sys.path.insert(0, os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "camera")))
    import hand_mapping as hm
    # Each axis's OWN output span over its OWN input window. Only the four
    # fingers use the full scale; both thumb axes are mapped onto the travel
    # they actually have, and taking the global span for them would report a
    # gain several times the truth - which lands as a deadband several times
    # too coarse on exactly the axes that need it most. thumb_bend went from
    # 816 counts to 235 on 2026-08-07 when its travel was measured, so this
    # is not hypothetical.
    finger = (hm.T_MAX - hm.T_MIN) / abs(hm.CURL_CLOSED - hm.CURL_OPEN)
    return {
        **{f: finger for f in FINGERS},
        "thumb_bend": ((hm.BEND_MAX - hm.BEND_MIN)
                       / abs(hm.THUMB_CLOSED - hm.THUMB_OPEN)),
        "thumb_rot": (hm.T_MAX - hm.ROT_MIN) / abs(hm.OPP_MAX - hm.OPP_MIN),
    }


# ---- the filter ----------------------------------------------------------

class HandFilter:
    """Six-axis continuous target in, six-axis continuous target out.

    Usage is one call per frame, and the caller sends only when told to:

        filt = HandFilter.for_camera()
        out = filt.update(raw_targets, now)
        if filt.changed:
            sink.send(out)

    `out` is six values - the current commanded pose, which is the last
    released one while the gate is holding - or None while some axis has
    never been measured. `changed` says whether the pose differs from what
    the caller was last told to send, so a still hand produces no traffic
    at all rather than traffic that happens to repeat.

    Why the gate is per-axis: hand_sink._moved_enough takes the max over
    all six axes, so one noisy axis releases the whole vector, including
    the five that moved a count. thumb_rot is the noisy one - 2.15 deg
    against 1.0-1.8 for the rest - and it alone released the old gate on
    64% of frames. Its noise is statistically independent of the fingers'
    (|r| <= 0.11 on increments), so gating per axis removes all of its
    contribution to finger travel at no latency cost. That is the cheapest
    win in this module and it is not a filter at all.
    """

    def __init__(self, gains, mincutoff=MINCUTOFF, beta=BETA,
                 dcutoff=DCUTOFF, deg=DEADBAND_DEG, dt_max=DT_MAX):
        self.gains = dict(gains)
        self.deadbands = deadbands(self.gains, deg)
        self.dt_max = dt_max
        self._filters = [_OneEuro(mincutoff, beta, dcutoff) for _ in AXES]
        self._last_t = None
        self._out = None        # what the filter currently believes
        self._sent = None       # what the caller was last told to send
        self.changed = False

    @classmethod
    def for_camera(cls, **kw):
        """Preset for the MediaPipe path, with that mapping's own gains."""
        return cls(camera_gains(), **kw)

    def set_deadband_deg(self, deg):
        """Retune the hysteresis live, in input degrees.

        This is the one parameter that is an operator preference rather
        than a measurement - "how much hand tremor do I tolerate" - and it
        is monotonic and cliff-free, so a slider cannot put the filter
        somewhere strange. The one-euro parameters are deliberately NOT
        exposed that way: they interact, and beta=0 at this mincutoff is a
        450 ms cliff that looks from the outside like a reasonable
        simplification.
        """
        self.deadbands = deadbands(self.gains, deg)

    def reset(self):
        """Forget everything. For a source that has been disconnected -
        NOT for a dropped frame, which is what dt_max is for."""
        for f in self._filters:
            f.reset()
        self._last_t = self._out = self._sent = None
        self.changed = False

    def update(self, targets, now):
        """One frame. `now` is monotonic seconds; the caller owns the clock.

        An axis whose value is HOLD is one the source could not measure -
        MediaPipe guessing at an occluded thumb, say. It freezes: the
        filter state is left alone rather than fed a substitute, because
        feeding it the last output would let a hold decay into the
        estimate and slowly become a real command.
        """
        if len(targets) != len(AXES):
            raise ValueError(f"expected {len(AXES)} targets, got {len(targets)}")

        if self._last_t is None:
            dt = None
        else:
            dt = min(max(now - self._last_t, DT_MIN), self.dt_max)
        self._last_t = now

        out = list(self._out) if self._out is not None else [None] * len(AXES)
        for i, x in enumerate(targets):
            if x == HOLD:
                continue                        # freeze: no state, no output
            if dt is None:
                out[i] = self._filters[i].update(x, DT_MIN)
            else:
                out[i] = self._filters[i].update(x, dt)
        if any(v is None for v in out):
            # Some axis has never been measured - a first frame that arrived
            # already holding. There is no pose yet, so say so rather than
            # handing back a vector with holes in it: callers index every
            # axis (the UI gauge does, and crashed doing it), and a partial
            # pose that looks like a pose is worse than no pose.
            self._out = out
            self.changed = False
            return None
        self._out = out

        if self._sent is None:
            self._sent = list(out)
            self.changed = True
        else:
            moved = False
            for i, a in enumerate(AXES):
                if abs(out[i] - self._sent[i]) > self.deadbands[a]:
                    self._sent[i] = out[i]
                    moved = True
            self.changed = moved
        return list(self._sent)
