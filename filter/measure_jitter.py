#!/usr/bin/env python3
"""Measure how much the vision chain moves while the operator holds still.

This is step 0 of the filter stage: the numbers that `hand_filter` will be
tuned against. It records and it analyses; it never opens a sink, so no
hand can move while it runs.

    # hold your hand still in front of the camera for 20 s
    python3 measure_jitter.py record --device=0 --seconds=20 -o still.csv

    # then tune offline, as many times as you like, with no camera
    python3 measure_jitter.py analyse still.csv

Recording and analysis are split on purpose. What goes in the CSV is the
*raw* mapping output plus the input angles that produced it, so every
filter and every threshold can be swept afterwards against the same
frames. Re-recording to try a different alpha would compare two different
noise realisations and prove nothing.

What the analysis answers, in order:

  1. How noisy is each axis when nothing is moving - in ANGLEACT counts,
     and in the input degrees they came from.
  2. Would the current gate fire? `hand_sink._moved_enough` takes the max
     over all six axes, so one noisy axis releases the whole vector. The
     blame column says which axis is doing it.
  3. What would per-axis gating, EMA and one-euro do instead - swept over
     a range of parameters, scored by commanded travel.

Filters and gates are swept TOGETHER, because they do not compose: a
filter that drops an axis below its own per-axis threshold silences it,
while the coupled gate still releases that axis whenever another one
moves. Scoring filters through today's gate and gates on unfiltered data
would never score the pairing that is actually being built.

With `record --telemetry` the recorder also logs what the hand reports on
each frame. It opens no sink either way, so that telemetry is the hand
sitting IDLE - a noise floor and a resting current to measure against, not
the hand's answer to these commands. Watching it answer needs a run that
actually drives it, which is a different recording.

`travel` is the headline number: the total commanded movement summed over
all six axes for the whole recording. The hand was still, so the correct
answer is zero and anything above it is jitter being sent to a motor.
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "camera")))

import hand_mapping as hm

AXES = ("pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot")
FINGERS = ("pinky", "ring", "middle", "index")

#: what hand_sink.Sink.deadband is today, so the sweep always brackets it
CURRENT_DEADBAND = 12

#: input tolerance the headline per-axis gate is derived from, in degrees
SWEEP_DEG = 1.5

MAP_FIELDS = (["t", "seen", "trust", "why"]
              + [f"tgt_{a}" for a in AXES]
              + [f"curl_{f}" for f in FINGERS]
              + ["thumb_flexion", "opposition"])

#: filled only with `record --telemetry`: what the hand reported on the same
#: frame. Read-only - the recorder asks the daemon for `state` and never
#: sends a target, so this does not weaken the promise that nothing moves.
TELE_FIELDS = [f"ang_{a}" for a in AXES] + [f"cur_{a}" for a in AXES]

#: the counts-per-input-degree in force WHEN THE RECORDING WAS MADE.
#:
#: Stamped because gains() reads the live mapping, and a calibration saved
#: between recording and analysis silently re-scales every threshold derived
#: from it - applying today's deadbands to yesterday's targets. That happened
#: on 2026-08-07: a calibration during the first hardware trial moved the
#: finger gain 6.04 -> 4.99 and thumb_rot 7.80 -> 4.59, and the three
#: recordings from that morning carry no stamp because this did not exist yet.
#: Constant down the column, which is the price of the file staying
#: self-contained when it is copied off the machine.
GAIN_FIELDS = [f"gain_{a}" for a in AXES]

FIELDS = MAP_FIELDS + TELE_FIELDS + GAIN_FIELDS


# ---- input-space gain ----------------------------------------------------
#
# Each axis maps a window of input degrees onto a window of ANGLEACT counts,
# and the windows are not the same width, so the same landmark noise reaches
# the motor multiplied by a different number on each axis. A deadband set in
# counts is therefore a different physical tolerance per axis; deriving it
# from these gains is what makes one number mean one thing.

def gains():
    """counts per input degree, per axis, read from the live mapping.

    Delegated to hand_filter for the same reason the filters are: the
    thresholds this file sweeps have to be the thresholds the filter
    builds, or the sweep is choosing a number for somebody else.
    """
    import hand_filter
    return hand_filter.camera_gains()


# ---- filters -------------------------------------------------------------
#
# Imported from hand_filter rather than defined here, and that direction
# matters: the instrument that picks the parameters has to be running the
# code that ships. Two copies drift, and the day they do, every number this
# file prints becomes a measurement of something nobody is using.
#
# Both are time-based. The EMA in teleop_app.py is not: it applies a fixed
# per-frame weight, so its time constant tracks whatever frame rate the
# machine happens to manage - about 77 ms at 30 FPS and 580 ms at the 3.9
# FPS MediaPipe falls to while re-detecting on the KD240. Same slider, seven
# times the smoothing. Anything proposed as a replacement has to take dt.

from hand_filter import ema, one_euro          # noqa: E402  (after sys.path)


# ---- gates ---------------------------------------------------------------

def _result(sent, per_axis, blame):
    """Totals plus the per-axis breakdown.

    The total alone is misleading: it is dominated by whichever axis has the
    largest excursions, which hides the thing being complained about. Four
    fingers each twitching a little sum to less than one thumb axis moving a
    lot, and it is the fingers the operator watches. So every gate reports
    where its travel went, and `finger_travel` names the part that matters.
    """
    return {"sent": sent,
            "travel": sum(per_axis.values()),
            "finger_travel": sum(per_axis[f] for f in FINGERS),
            "axis_travel": per_axis,
            "blame": blame}


def gate_coupled(rows, deadband):
    """What ships today: max over six axes, so any one axis releases all six.

    Mirrors hand_sink.DaemonSink._moved_enough, including that it compares
    against the last *sent* vector rather than the last frame - that part is
    right, and is what makes the hold stable once the gate stops firing.
    """
    sent, last, blame = 0, None, {a: 0 for a in AXES}
    per_axis = {a: 0.0 for a in AXES}
    for r in rows:
        if last is None:
            last = list(r)
            sent += 1
            continue
        diffs = [abs(a - b) for a, b in zip(r, last)]
        if max(diffs) > deadband:
            for i, a in enumerate(AXES):
                per_axis[a] += diffs[i]
            blame[AXES[diffs.index(max(diffs))]] += 1
            last = list(r)
            sent += 1
    return _result(sent, per_axis, blame)


def gate_per_axis(rows, deadbands):
    """Each axis holds its own last-sent value and is released on its own."""
    sent, last = 0, None
    per_axis = {a: 0.0 for a in AXES}
    for r in rows:
        if last is None:
            last = list(r)
            sent += 1
            continue
        moved = False
        for i, a in enumerate(AXES):
            d = abs(r[i] - last[i])
            if d > deadbands[a]:
                per_axis[a] += d
                last[i] = r[i]
                moved = True
        sent += 1 if moved else 0
    return _result(sent, per_axis, {a: 0 for a in AXES})


# ---- scoring a filter ----------------------------------------------------

def as_rows(cols, n):
    """Six per-axis columns back into n six-axis frames."""
    return [[cols[i][k] for i in range(6)] for k in range(n)]


def p2p_fingers(cols):
    """Largest peak-to-peak excursion among the four fingers.

    `travel` is a path length: it counts every wiggle, which makes it the
    right proxy for motor duty and the wrong one for what the operator
    sees. Amplitude is what looks wrong, and a filter can cut path length
    hard while leaving the visible swing where it was. Fingers only, for
    the same reason finger_travel exists.

    Being an extremum it is maximally sensitive to single frames, so a
    p2p_f that refuses to fall while travel collapses means low-frequency
    content the filter cannot touch - and that is NOT automatically drift.
    On the first real still.csv it was a settling transient in the opening
    seconds, and reading it as drift would have been wrong: 118 counts
    became 37 once the head of the recording was skipped. Check the
    settling block before concluding anything from this column.
    """
    return max(max(cols[i]) - min(cols[i])
               for i, a in enumerate(AXES) if a in FINGERS)


# ---- what each path actually commands ------------------------------------
#
# Both of these return the STAIRCASE - the pose the hand was actually
# holding at each frame - rather than a smooth curve. That is the honest
# thing to draw: a gate holds its last released value, so what reaches the
# motor is a series of steps, and a plot of the underlying smooth signal
# would show a filter doing work the hand never sees.

def commanded_old(tgts, deadband=CURRENT_DEADBAND):
    """The pre-filter path: raw targets, one max-over-six-axes deadband."""
    out, last = [], None
    for r in tgts:
        if last is None or max(abs(a - b) for a, b in zip(r, last)) > deadband:
            last = list(r)
        out.append(list(last))
    return out


def commanded_new(tgts, ts, trust, gains_):
    """The shipped path, driven exactly as teleop_app drives it.

    `gains_` is the recording's own, not the live mapping's: the deadbands
    have to be the ones that were in force when these targets were computed.
    """
    import hand_filter as hf
    filt = hf.HandFilter(gains_)
    out, last = [], None
    for r, t, tr in zip(tgts, ts, trust):
        raw = list(r)
        if not tr:
            raw[4] = raw[5] = hf.HOLD
        v = filt.update(raw, t)
        if v is not None:
            last = v
        out.append(list(last) if last is not None else None)
    return out


# ---- plotting ------------------------------------------------------------

def plot(args):
    """Overlay the two paths on the SAME frames.

    Pressing 'f' in teleop cannot produce this picture: filter on and
    filter off are two different stretches of time in which the operator's
    hand did two different things, so overlaying them compares two inputs
    rather than two filters. Replaying one recording through both paths is
    the only version of this comparison that means anything - the same
    reason record and analyse are separate commands at all.
    """
    import matplotlib
    matplotlib.use("Agg")                      # no display on any of these hosts
    import matplotlib.pyplot as plt

    # One panel per (recording, axis). Showing every axis is the answer to
    # the first question anyone asks of a single-axis figure, which is
    # whether that axis was chosen because it flattered the result.
    panels = []
    for spec in args.csv:
      for axis in args.axis:
        ax_i = AXES.index(axis)
        path, _, zoom = spec.partition("@")
        rows, ts, _, tgts, _ = load(path, args.skip)
        g, stamped = recorded_gains(rows, path)
        if args.gain:
            g = dict(g, **{a: args.gain[i] for i, a in enumerate(AXES)})
        gain = g[axis]
        trust = [r.get("trust") == "1" for r in rows]
        t0 = ts[0]
        ts = [t - t0 for t in ts]
        old = commanded_old(tgts)
        new = commanded_new(tgts, [t + t0 for t in ts], trust, g)
        # ANGLEACT, but only from a run log - a `sent` column means the hand
        # was being driven while this was recorded. In a measure_jitter
        # recording nothing is commanded, so the same column is the hand
        # sitting idle and drawing it here would be a flat line pretending
        # to be a response.
        act = None
        if rows and rows[0].get("sent") is not None and rows[0].get("ang_pinky"):
            try:
                act = [[float(r[f"ang_{a}"]) if r.get(f"ang_{a}") else None
                        for a in AXES] for r in rows]
            except ValueError:
                act = None
        panels.append((os.path.basename(path), axis, ax_i, zoom, ts, tgts,
                       old, new, act, gain, stamped or bool(args.gain)))

    fig, axes = plt.subplots(len(panels), 1, figsize=(11, 3.1 * len(panels)),
                             squeeze=False)
    for k, (name, axis, ax_i, zoom, ts, tgts, old, new, act, gain,
            stamped) in enumerate(panels):
        ax = axes[k][0]
        lo, hi = (0, ts[-1])
        if zoom:
            c, w = (float(v) for v in zoom.split(":"))
            lo, hi = c - w / 2, c + w / 2
        sel = [i for i, t in enumerate(ts) if lo <= t <= hi]
        tt = [ts[i] for i in sel]

        ax.plot(tt, [tgts[i][ax_i] for i in sel], color="#c9c9c9", lw=0.8,
                zorder=1, label="raw from the camera")
        ax.plot(tt, [old[i][ax_i] for i in sel], color="#d1495b", lw=1.3,
                zorder=3, label="filter OFF - what ships today")
        ax.plot(tt, [new[i][ax_i] if new[i] else float("nan") for i in sel],
                color="#0a7d6b", lw=1.7, zorder=4, label="filter ON")
        if act is not None:
            ax.plot(tt, [act[i][ax_i] if act[i][ax_i] is not None
                         else float("nan") for i in sel],
                    color="#26439c", lw=1.2, ls=(0, (4, 2)), zorder=5,
                    label="ANGLEACT - where the hand got to (measured)")

        def travel(seq):
            # Skip the leading stretch where the filter has no pose yet - an
            # axis that has never been measured has no value to travel from,
            # and a run that opens on an untrusted thumb starts exactly
            # there. The traces already draw those frames as gaps.
            tot = 0.0
            for j in range(1, len(sel)):
                a, b = seq[sel[j]], seq[sel[j - 1]]
                if a is None or b is None:
                    continue
                tot += abs(a[ax_i] - b[ax_i])
            return tot

        t_old, t_new = travel(old), travel(new)
        cut = 100 * (1 - t_new / t_old) if t_old else 0
        ax.set_title(f"{name}   {axis}   commanded travel "
                     f"{t_old:.0f} -> {t_new:.0f} counts  ({cut:.0f}% less)",
                     fontsize=10, loc="left")
        # Counts only. A second axis in absolute degrees would be wrong:
        # the mapping is affine - 1034 counts is 150 degrees of curl, not
        # zero - so counts/gain is meaningful for a DIFFERENCE and nonsense
        # for a value. The gain goes in the label, where it says the one
        # true thing: how many counts an input degree is worth.
        ax.set_ylabel(f"ANGLEACT counts\n({gain:.1f} counts = 1 input degree)",
                      fontsize=9)
        if not stamped:
            ax.text(0.995, 0.03, "gains not stamped in this recording",
                    transform=ax.transAxes, ha="right", fontsize=7,
                    color="#b06000")
        ax.grid(alpha=0.25, lw=0.5)
        ax.set_xlim(lo, hi)
        if k == 0:
            ax.legend(loc="upper left", fontsize=8, framealpha=0.9,
                      bbox_to_anchor=(0.0, -0.02))
    axes[-1][0].set_xlabel("seconds")
    fig.tight_layout()
    fig.savefig(args.out, dpi=args.dpi)
    print(f"wrote {args.out}")


# ---- recording -----------------------------------------------------------

def open_telemetry():
    """A read-only handd connection, or None with the reason printed.

    `state` reads the input PDO image; it commands nothing. No target is
    ever sent from this file, so the hand stays as still during a
    telemetry recording as it does without one. A daemon that is not
    running is not an error - the mapping half of the recording is still
    worth having.
    """
    sys.path.insert(0, os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "hand_fw")))
    try:
        import hand_client
        c = hand_client.HandClient()
        c.connect()
    except Exception as e:                     # noqa: BLE001 - any failure
        print(f"telemetry unavailable ({e}) - recording the mapping only")
        return None
    if c.info.get("simulate"):
        print("telemetry: handd is SIMULATED - these are not a real hand's"
              " sensors")
    else:
        print("telemetry: reading ANGLEACT and current from handd")
    return c


def record(args):
    import cv2
    import mediapipe as mp

    cap = cv2.VideoCapture(args.device)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        sys.exit(f"camera {args.device} did not open")

    hands = mp.solutions.hands.Hands(max_num_hands=1,
                                     min_detection_confidence=0.6,
                                     min_tracking_confidence=0.5)
    win = "measure_jitter - HOLD STILL"
    if not args.no_window:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    tele = open_telemetry() if args.telemetry else None
    gain_row = [f"{gains()[a]:.4f}" for a in AXES]

    out = open(args.out, "w", newline="")
    w = csv.writer(out)
    w.writerow(FIELDS)

    import time
    t0 = time.perf_counter()
    n = seen = 0
    print(f"recording {args.seconds} s to {args.out} - hold your hand still")
    try:
        while True:
            t = time.perf_counter() - t0
            if t >= args.seconds:
                break
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            n += 1
            row = [f"{t:.4f}", 0, 0, ""] + [""] * (len(MAP_FIELDS) - 4)
            if res.multi_hand_landmarks and res.multi_hand_world_landmarks:
                world = res.multi_hand_world_landmarks[0].landmark
                image = res.multi_hand_landmarks[0].landmark
                handed = res.multi_handedness[0].classification[0]
                trust, why = hm.thumb_trust(image, handed.label, handed.score)
                tgt = hm.pose_from_world_landmarks(world)
                curls = [hm.finger_curl(world, hm.FINGER_CHAINS[f]) for f in FINGERS]
                tf = hm.thumb_features(world)
                row = ([f"{t:.4f}", 1, int(trust), why]
                       + [str(v) for v in tgt]
                       + [f"{c:.3f}" for c in curls]
                       + [f"{tf['flexion']:.3f}", f"{tf['opposition']:.3f}"])
                seen += 1
            tele_row = [""] * len(TELE_FIELDS)
            if tele is not None:
                try:
                    st = tele.state()
                    if st.get("bus") == "up":
                        tele_row = ([str(v) for v in st["ang"]]
                                    + [str(v) for v in st["cur"]])
                except Exception:              # noqa: BLE001
                    pass    # a telemetry gap must not end a 20 s recording
            w.writerow(row + tele_row + gain_row)
            if not args.no_window:
                left = args.seconds - t
                cv2.putText(frame, f"HOLD STILL  {left:4.1f}s",
                            (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                            (0, 200, 255), 2)
                cv2.putText(frame, f"frames {n}  hand seen {seen}",
                            (12, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (220, 220, 220), 1)
                cv2.imshow(win, frame)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
    finally:
        cap.release()
        out.close()
        if tele is not None:
            tele.close()
        if not args.no_window:
            cv2.destroyAllWindows()

    print(f"{n} frames, hand seen in {seen} ({100.0 * seen / max(n, 1):.0f}%)")
    if seen < 30:
        print("WARNING: too few frames with a hand in them to say anything")
    print(f"now run:  python3 {os.path.basename(__file__)} analyse {args.out}")


# ---- analysis ------------------------------------------------------------

def load(path, skip=0.0):
    """Frames with a hand in them, dropping the first `skip` seconds.

    The head of a recording is the operator arriving in frame and
    MediaPipe converging on them, not a still hand. Measured on the first
    real still.csv, 2026-08-07: the index axis reads sd 17.3 counts over
    the whole twenty seconds and 6.3 from four seconds on, and its p2p
    falls 118 -> 37. Folding that in inflates every threshold derived from
    it, and an over-smoothed filter pays for the artefact in latency.
    """
    with open(path) as f:
        rows = [r for r in csv.DictReader(f) if r["seen"] == "1"]
    if not rows:
        sys.exit(f"{path} has no frames with a hand in them")
    t0 = float(rows[0]["t"])
    kept = [r for r in rows if float(r["t"]) - t0 >= skip]
    if not kept:
        sys.exit(f"--skip={skip} left no frames of {path}")
    ts = [float(r["t"]) for r in kept]
    tgts = [[float(r[f"tgt_{a}"]) for a in AXES] for r in kept]
    dts = [max(1e-3, ts[i] - ts[i - 1]) if i else 0.033 for i in range(len(ts))]
    return kept, ts, dts, tgts, len(rows) - len(kept)


def settling(ts, tgts, blocks=5):
    """Per-block sd per axis, so an unsettled recording cannot pass as noise.

    A hand that was actually still reads about the same in every block. A
    first block several times the last is the recording still converging,
    and it is worth seeing rather than averaging away - this is the check
    that would have caught the 2026-08-07 still.csv before its numbers were
    used for anything.
    """
    n = len(tgts)
    b = max(1, n // blocks)
    out = []
    for k in range(blocks):
        lo = k * b
        hi = n if k == blocks - 1 else min((k + 1) * b, n)
        if lo >= hi:
            continue
        out.append(((ts[lo], ts[hi - 1]),
                    [stats([s[i] for s in tgts[lo:hi]])[1] for i in range(6)]))
    return out


def recorded_gains(rows, path):
    """The gains the recording was made with, or the live ones with a warning.

    An unstamped recording predates the stamp; it cannot be known which
    calibration produced it, so say that out loud rather than quietly
    scoring it against whatever is active today.
    """
    key = f"gain_{AXES[0]}"
    if rows and rows[0].get(key):
        try:
            return {a: float(rows[0][f"gain_{a}"]) for a in AXES}, True
        except (KeyError, ValueError):
            pass
    return gains(), False


def telemetry(rows):
    """Per-axis (ANGLEACT, current) for the frames that carry it, or None.

    Present only if the recording was made with --telemetry, and blank on
    any frame where the daemon's bus was down, so the frames are counted
    rather than assumed.
    """
    key = f"ang_{AXES[0]}"
    if not rows or key not in rows[0]:
        return None
    ang, cur = [], []
    for r in rows:
        if not r.get(key):
            continue
        try:
            ang.append([float(r[f"ang_{a}"]) for a in AXES])
            cur.append([float(r[f"cur_{a}"]) for a in AXES])
        except (KeyError, ValueError):
            continue
    return (ang, cur) if ang else None


def stats(col):
    n = len(col)
    mean = sum(col) / n
    var = sum((v - mean) ** 2 for v in col) / n
    return mean, math.sqrt(var), max(col) - min(col)


def analyse(args):
    _, ts_all, _, tgts_all, _ = load(args.csv, 0.0)
    rows, ts, dts, tgts, dropped = load(args.csv, args.skip)
    n = len(rows)
    span = ts[-1] - ts[0]
    g, stamped = recorded_gains(rows, args.csv)
    if not stamped:
        print(f"\n!! {args.csv} carries no gain stamp, so it is being scored"
              f"\n   against the CALIBRATION ACTIVE NOW"
              f" ({hm.ACTIVE_PROFILE or 'module defaults'})."
              f"\n   If that is not the one it was recorded under, every"
              f" threshold below is\n   wrong by the ratio. Re-record to"
              f" stamp it, or pass --gain-deg.")

    print("\n-- did the recording settle? --")
    print("  sd per axis over five blocks. A hand that was still reads the"
          " same in\n  every one; a loud first block is the operator arriving"
          " and MediaPipe\n  converging, and it belongs in no noise figure.")
    print(f"{'block':<14}" + "".join(f"{a[:9]:>10}" for a in AXES))
    blocks = settling(ts_all, tgts_all)
    for (lo, hi), sds in blocks:
        print(f"{f'{lo:5.1f}-{hi:4.1f}s':<14}"
              + "".join(f"{v:>10.1f}" for v in sds))
    if len(blocks) > 1 and max(blocks[0][1]) > 2.0 * max(blocks[-1][1]):
        print(f"  the first block is {max(blocks[0][1])/max(blocks[-1][1]):.1f}x"
              " the last: this recording had not settled when it started")
    print(f"\n  --skip={args.skip}s drops {dropped} of {dropped + n} frames"
          + ("  (--skip=0 to keep them)" if dropped else ""))

    print(f"\n{args.csv}: {n} frames with a hand, {span:.1f} s")
    live = [d for d in dts[1:]]
    live.sort()
    fps = n / span if span > 0 else 0
    print(f"frame rate {fps:.1f} FPS   dt median {live[len(live)//2]*1000:.0f} ms"
          f"   p95 {live[int(len(live)*0.95)]*1000:.0f} ms")
    trusted = sum(1 for r in rows if r["trust"] == "1")
    print(f"thumb trusted on {trusted}/{n} frames ({100.0*trusted/n:.0f}%)")

    print("\n-- per-axis noise while holding still --")
    print(f"{'axis':<12}{'mean':>8}{'sd':>8}{'p2p':>8}{'sd(deg)':>10}"
          f"{'gain':>8}{'|dframe|':>10}")
    for i, a in enumerate(AXES):
        col = [t[i] for t in tgts]
        mean, sd, p2p = stats(col)
        deltas = [abs(col[k] - col[k - 1]) for k in range(1, len(col))]
        dmean = sum(deltas) / max(len(deltas), 1)
        print(f"{a:<12}{mean:8.0f}{sd:8.1f}{p2p:8.0f}{sd/g[a]:10.2f}"
              f"{g[a]:8.1f}{dmean:10.1f}")
    print("  sd/p2p/|dframe| are ANGLEACT counts; sd(deg) is the same noise"
          " back in input degrees")

    tele = telemetry(rows)
    if tele:
        ang, cur = tele
        print(f"\n-- the hand while nothing drove it ({len(ang)}/{n} frames"
              " carry telemetry) --")
        print("  Recording opens no sink, so this is the hand sitting idle,"
              " not the hand\n  answering these commands. Two things it is"
              " for: a command jitter below\n  the hand's own ANGLEACT noise"
              " is below what the hand can even resolve,\n  and this current"
              " is the zero line a driven run gets compared against.")
        print(f"{'axis':<12}{'cmd sd':>9}{'ang sd':>9}{'ang p2p':>9}"
              f"{'cur mean':>10}{'cur max':>9}")
        for i, a in enumerate(AXES):
            _, cmd_sd, _ = stats([t[i] for t in tgts])
            _, a_sd, a_p2p = stats([v[i] for v in ang])
            cm = [abs(v[i]) for v in cur]
            print(f"{a:<12}{cmd_sd:>9.1f}{a_sd:>9.1f}{a_p2p:>9.0f}"
                  f"{sum(cm)/len(cm):>10.1f}{max(cm):>9.0f}")
        louder = [a for i, a in enumerate(AXES)
                  if stats([t[i] for t in tgts])[1]
                  <= stats([v[i] for v in ang])[1]]
        if louder:
            print("  at or below the hand's own sensor noise: "
                  + ", ".join(louder)
                  + "\n  - filtering those buys nothing observable")
        print("  a hand nobody is commanding should draw no current; whatever"
              " is here is\n  the resting draw, and the number a jitter run"
              " has to be measured against")

    print("\n-- would the current gate fire? --")
    cur = gate_coupled(tgts, CURRENT_DEADBAND)
    print(f"deadband={CURRENT_DEADBAND} (today's value), coupled max-over-axes:")
    print(f"  {cur['sent']}/{n} frames sent a pose ({100.0*cur['sent']/n:.0f}%),"
          f" commanded travel {cur['travel']:.0f} counts")
    print(f"  of which the four fingers: {cur['finger_travel']:.0f} counts")
    blame = sorted(cur["blame"].items(), key=lambda kv: -kv[1])
    if cur["sent"] > 1:
        print("  released by: " + ", ".join(
            f"{a} {100.0*c/max(cur['sent']-1,1):.0f}%" for a, c in blame if c))
    print("  (the hand was still, so every one of those counts is jitter)")
    loudest = max(cur["blame"], key=cur["blame"].get)
    if cur["blame"][loudest] > 0.5 * max(cur["sent"] - 1, 1):
        print(f"  {loudest} alone releases the gate on over half the frames,"
              f" and drags the\n  other five with it - that is the coupling,"
              f" measured")

    print("\n-- deadband sweep --")
    print("  'fingers' is the travel commanded to the four fingers alone")
    print(f"{'deadband':>9}{'sent':>8}{'travel':>10}{'fingers':>10}"
          f"{'sent':>10}{'travel':>10}{'fingers':>10}")
    print(f"{'':>9}{'-- coupled (today) --':^28}{'-- per-axis --':^30}")
    for db in (0, 4, 8, 12, 16, 24, 32, 48):
        c = gate_coupled(tgts, db)
        p = gate_per_axis(tgts, {a: db for a in AXES})
        print(f"{db:>9}{c['sent']:>8}{c['travel']:>10.0f}{c['finger_travel']:>10.0f}"
              f"{p['sent']:>10}{p['travel']:>10.0f}{p['finger_travel']:>10.0f}")

    print("\n-- per-axis deadband from a single input tolerance --")
    print("  one tolerance in degrees, scaled by each axis's own gain")
    print(f"{'deg':>6}  " + "".join(f"{a:>11}" for a in AXES)
          + f"{'sent':>8}{'travel':>10}")
    for deg in (0.5, 1.0, 1.5, 2.0, 3.0):
        dbs = {a: deg * g[a] for a in AXES}
        p = gate_per_axis(tgts, dbs)
        print(f"{deg:>6.1f}  " + "".join(f"{dbs[a]:>11.0f}" for a in AXES)
              + f"{p['sent']:>8}{p['travel']:>10.0f}")

    print("\n-- filters x gates --")
    print("  The design on the table is a filter feeding a PER-AXIS gate, so"
          "\n  the two have to be scored together. They do not compose: a"
          " filter that\n  drops one axis below its own threshold sends"
          " nothing on it, while the\n  coupled gate still releases that axis"
          " whenever another one moves.\n  Reading either column alone"
          " predicts the wrong thing.")
    dbs = {a: args.deg * g[a] for a in AXES}
    print(f"\n  per-axis thresholds, {args.deg} deg scaled by each axis's"
          " gain: "
          + ", ".join(f"{a} {dbs[a]:.0f}" for a in AXES))
    print(f"\n{'':<26}{'--- coupled, db=' + str(CURRENT_DEADBAND) + ' ---':^25}"
          f"{'--- per-axis ---':^26}{'':>8}")
    print(f"{'filter':<26}{'sent':>7}{'travel':>9}{'fingers':>9}"
          f"{'sent':>8}{'travel':>9}{'fingers':>9}{'p2p_f':>8}")

    def score(name, cols):
        f = as_rows(cols, n)
        c = gate_coupled(f, CURRENT_DEADBAND)
        p = gate_per_axis(f, dbs)
        print(f"{name:<26}{c['sent']:>7}{c['travel']:>9.0f}"
              f"{c['finger_travel']:>9.0f}{p['sent']:>8}{p['travel']:>9.0f}"
              f"{p['finger_travel']:>9.0f}{p2p_fingers(cols):>8.0f}")

    raw_cols = [[t[i] for t in tgts] for i in range(6)]
    score("raw (no filter)", raw_cols)

    import hand_filter as hf
    score(f"SHIPPED fc={hf.MINCUTOFF} b={hf.BETA}",
          [one_euro(c, dts, hf.MINCUTOFF, hf.BETA, hf.DCUTOFF)
           for c in raw_cols])

    for tau in (0.05, 0.1, 0.2, 0.4):
        score(f"ema tau={tau}s", [ema(c, dts, tau) for c in raw_cols])
    # beta reaches down to 5e-4 because that is where the useful setting
    # turned out to live, an order of magnitude below the smallest value a
    # coarser sweep had tried - and a grid that does not contain the answer
    # reports, quite convincingly, that the answer does not exist.
    for mc in (0.05, 0.5, 1.0, 2.0):
        for beta in (0.0, 0.0005, 0.005, 0.02):
            score(f"one-euro fc={mc} b={beta}",
                  [one_euro(c, dts, mc, beta) for c in raw_cols])
    print("\n  p2p_f is the largest peak-to-peak swing among the four fingers"
          " AFTER\n  filtering: travel is a path length and amplitude is what"
          " the operator\n  sees, and a filter can cut one without the other."
          " A p2p_f that will not\n  fall while travel collapses is slow"
          " drift, which is real hand movement\n  and not the filter's to"
          " remove.")
    print("\n  THIS TABLE CANNOT CHOOSE A FILTER. On a still recording more"
          " smoothing\n  always scores better, without limit, so the best row"
          " here is only ever\n  the most aggressive row offered. It says how"
          " much jitter a setting\n  removes; what that costs is lag, it is"
          " not on this page, and it needs\n  the moving recording. Read the"
          " SHIPPED row as a reference point - has\n  the noise moved since"
          " those parameters were chosen - and not as a\n  contestant that"
          " won or lost.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="record a still-hand run to CSV")
    rec.add_argument("--device", type=int, default=0)
    rec.add_argument("--seconds", type=float, default=20.0)
    rec.add_argument("--width", type=int, default=640)
    rec.add_argument("--height", type=int, default=480)
    rec.add_argument("--no-window", action="store_true",
                     help="no preview - the operator gets no feedback either")
    rec.add_argument("--telemetry", action="store_true",
                     help="also log the hand's ANGLEACT and current from "
                          "handd. Read-only: no target is ever sent, so the "
                          "hand stays as still as it does without this")
    rec.add_argument("-o", "--out", default="still.csv")
    rec.set_defaults(fn=record)

    an = sub.add_parser("analyse", help="report noise and sweep thresholds")
    an.add_argument("csv")
    an.add_argument("--skip", type=float, default=2.0,
                    help="drop this many seconds off the front, where the "
                         "recording is still settling (default 2.0, 0 keeps "
                         "everything). The settling block shows what it cost")
    an.add_argument("--deg", type=float, default=SWEEP_DEG,
                    help="input tolerance the per-axis gate column is built "
                         f"from, in degrees (default {SWEEP_DEG})")
    an.set_defaults(fn=analyse)

    pl = sub.add_parser("plot", help="overlay filter off vs on, same frames")
    pl.add_argument("csv", nargs="+",
                    help="one panel per recording. Append @centre:width in "
                         "seconds to zoom, e.g. dropout.csv@14.6:3")
    pl.add_argument("--axis", nargs="+", default=["ring"],
                    choices=AXES,
                    help="one panel per recording per axis")
    pl.add_argument("--skip", type=float, default=2.0)
    pl.add_argument("-o", "--out", default="filter_ab.png")
    pl.add_argument("--dpi", type=int, default=150)
    pl.add_argument("--gain", type=float, nargs=6, default=None,
                    metavar="G",
                    help="counts per input degree for the six axes, for "
                         "recordings made before the stamp existed. Order: "
                         + " ".join(AXES))
    pl.set_defaults(fn=plot)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
