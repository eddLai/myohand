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

FIELDS = (["t", "seen", "trust", "why"]
          + [f"tgt_{a}" for a in AXES]
          + [f"curl_{f}" for f in FINGERS]
          + ["thumb_flexion", "opposition"])


# ---- input-space gain ----------------------------------------------------
#
# Each axis maps a window of input degrees onto a window of ANGLEACT counts,
# and the windows are not the same width, so the same landmark noise reaches
# the motor multiplied by a different number on each axis. A deadband set in
# counts is therefore a different physical tolerance per axis; deriving it
# from these gains is what makes one number mean one thing.

def gains():
    """counts per input degree, per axis, read from the live mapping."""
    span = hm.T_MAX - hm.T_MIN
    finger = span / abs(hm.CURL_CLOSED - hm.CURL_OPEN)
    return {
        **{f: finger for f in FINGERS},
        "thumb_bend": span / abs(hm.THUMB_CLOSED - hm.THUMB_OPEN),
        "thumb_rot": (hm.T_MAX - hm.ROT_MIN) / abs(hm.OPP_MAX - hm.OPP_MIN),
    }


# ---- filters -------------------------------------------------------------
#
# Both are time-based. The EMA in teleop_app.py is not: it applies a fixed
# per-frame weight, so its time constant tracks whatever frame rate the
# machine happens to manage - about 77 ms at 30 FPS and 580 ms at the 3.9
# FPS MediaPipe falls to while re-detecting on the KD240. Same slider, seven
# times the smoothing. Anything proposed as a replacement has to take dt.

def ema(series, dts, tau):
    """Exponential smoothing with a real time constant, in seconds."""
    out, y = [], None
    for x, dt in zip(series, dts):
        if y is None:
            y = x
        else:
            a = 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0
            y += a * (x - y)
        out.append(y)
    return out


def one_euro(series, dts, mincutoff=1.0, beta=0.0, dcutoff=1.0):
    """Cutoff rises with speed: heavy smoothing when still, light when moving.

    The trade-off a fixed alpha cannot escape - jitter when still or lag
    when moving, pick one - is exactly what this filter was designed for,
    and noisy 3D tracking driving an actuator is the case it was designed
    on. beta=0 degenerates to a plain low-pass at mincutoff.
    """
    def alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    out, x_hat, dx_hat = [], None, 0.0
    for x, dt in zip(series, dts):
        if x_hat is None:
            x_hat = x
        else:
            dx = (x - x_hat) / dt
            dx_hat += alpha(dcutoff, dt) * (dx - dx_hat)
            cutoff = mincutoff + beta * abs(dx_hat)
            x_hat += alpha(cutoff, dt) * (x - x_hat)
        out.append(x_hat)
    return out


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


# ---- recording -----------------------------------------------------------

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
            row = [f"{t:.4f}", 0, 0, ""] + [""] * (len(FIELDS) - 4)
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
            w.writerow(row)
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
        if not args.no_window:
            cv2.destroyAllWindows()

    print(f"{n} frames, hand seen in {seen} ({100.0 * seen / max(n, 1):.0f}%)")
    if seen < 30:
        print("WARNING: too few frames with a hand in them to say anything")
    print(f"now run:  python3 {os.path.basename(__file__)} analyse {args.out}")


# ---- analysis ------------------------------------------------------------

def load(path):
    with open(path) as f:
        rows = [r for r in csv.DictReader(f) if r["seen"] == "1"]
    if not rows:
        sys.exit(f"{path} has no frames with a hand in them")
    ts = [float(r["t"]) for r in rows]
    tgts = [[float(r[f"tgt_{a}"]) for a in AXES] for r in rows]
    dts = [max(1e-3, ts[i] - ts[i - 1]) if i else 0.033 for i in range(len(ts))]
    return rows, ts, dts, tgts


def stats(col):
    n = len(col)
    mean = sum(col) / n
    var = sum((v - mean) ** 2 for v in col) / n
    return mean, math.sqrt(var), max(col) - min(col)


def analyse(args):
    rows, ts, dts, tgts = load(args.csv)
    n = len(rows)
    span = ts[-1] - ts[0]
    g = gains()

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

    print("\n-- filters, scored on travel through today's gate --")
    print("  travel with no filter at all is the first row's baseline")
    print(f"{'filter':<28}{'sent':>8}{'travel':>10}{'vs raw':>9}")
    base = gate_coupled(tgts, CURRENT_DEADBAND)["travel"]
    print(f"{'raw (no filter)':<28}{cur['sent']:>8}{base:>10.0f}{'100%':>9}")

    def score(name, cols):
        filtered = [[cols[i][k] for i in range(6)] for k in range(n)]
        r = gate_coupled(filtered, CURRENT_DEADBAND)
        pct = 100.0 * r["travel"] / base if base else 0.0
        print(f"{name:<28}{r['sent']:>8}{r['travel']:>10.0f}{pct:>8.0f}%")

    for tau in (0.05, 0.1, 0.2, 0.4):
        score(f"ema tau={tau}s",
              [ema([t[i] for t in tgts], dts, tau) for i in range(6)])
    for mc in (0.5, 1.0, 2.0):
        for beta in (0.0, 0.005, 0.02):
            score(f"one-euro fc={mc} beta={beta}",
                  [one_euro([t[i] for t in tgts], dts, mc, beta)
                   for i in range(6)])
    print("\n  a filter that scores low on travel but was measured only on a"
          "\n  still hand says nothing about lag - that needs a moving"
          "\n  recording, which is the next measurement, not this one.\n")


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
    rec.add_argument("-o", "--out", default="still.csv")
    rec.set_defaults(fn=record)

    an = sub.add_parser("analyse", help="report noise and sweep thresholds")
    an.add_argument("csv")
    an.set_defaults(fn=analyse)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
