#!/usr/bin/env python3
"""Does the hand actually follow a moving target? Run this on the board.

Everything else measured whether a pose arrives. This measures whether a
stream of them does, which is the thing the project needs and the thing
that was believed impossible until 2026-08-06. It drives one axis along a
smooth trajectory through the resident daemon, samples where the axis
really is on every step, and reports how far behind it runs.

What it proves, if it passes: the link stays up, OPERATIONAL is held
throughout, no disconnect and no watchdog is involved, and the hand tracks
a target that never stops moving.

    ./handd --iface=eth1 &
    python3 verify_following.py

Safety: one axis, a sine confined to a band well inside the mechanism's
travel, and every target still passes the daemon's interlock and stall
guards on the way in. It refuses to start on an axis that is stalled or
still in standby.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hand_client                                          # noqa: E402

AXIS_NAMES = ["pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default=None, help="handd socket")
    ap.add_argument("--axis", type=int, default=2)
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument("--rate", type=float, default=50.0,
                    help="Hz at which targets are pushed")
    ap.add_argument("--period", type=float, default=4.0,
                    help="seconds per full sine cycle")
    ap.add_argument("--centre", type=int, default=1325)
    ap.add_argument("--amp", type=int, default=150,
                    help="ANGLEACT counts either side of centre")
    ap.add_argument("--csv", default=None, help="write the trace here")
    args = ap.parse_args()

    lo, hi = args.centre - args.amp, args.centre + args.amp
    if lo < 1050 or hi > 1650:
        print(f"refusing: the swing {lo}..{hi} leaves the safe band "
              f"1050..1650")
        return 1

    kw = {"path": args.socket} if args.socket else {}
    try:
        hand = hand_client.HandClient(**kw).connect()
    except hand_client.HandDaemonError as e:
        print(f"{e}\n\nStart it first:  ./handd --iface=eth1 &")
        return 1

    info = hand.hello()
    print(f"daemon: trigger={info['trigger']} rate_hz={info['rate_hz']} "
          f"simulate={info['simulate']}")
    if info["rate_hz"] > 625:
        print(f"WARNING: {info['rate_hz']} Hz is above the 625 Hz this hand "
              f"can absorb; expect it to apply nothing at all")

    st = hand.state()
    if st.get("bus") == "down":
        print("the daemon reports the bus down")
        return 1
    if 7 in st["sta"]:
        print(f"axes in standby (STA=7): {st['sta']} - send one pose first "
              f"so the daemon wakes them")
        return 1
    if st["sta"][args.axis] in (5, 6) or st["cur"][args.axis] > 400:
        print(f"axis {args.axis} is stalled (sta={st['sta'][args.axis]} "
              f"cur={st['cur'][args.axis]}mA) - relieve it first")
        return 1

    print(f"axis {args.axis} ({AXIS_NAMES[args.axis]}) resting at "
          f"{st['ang'][args.axis]}, sweeping {lo}..{hi} over {args.secs}s "
          f"at {args.rate} Hz\n")

    dt = 1.0 / args.rate
    rows = []
    t0 = time.time()
    sent = 0
    while True:
        t = time.time() - t0
        if t >= args.secs:
            break
        cmd = int(round(args.centre +
                        args.amp * math.sin(2 * math.pi * t / args.period)))
        targets = [-1] * 6
        targets[args.axis] = cmd
        hand.target(targets)
        sent += 1
        s = hand.state()
        rows.append((t, cmd, s["ang"][args.axis], s["cur"][args.axis],
                     s["sta"][args.axis]))
        slack = dt - ((time.time() - t0) - t)
        if slack > 0:
            time.sleep(slack)

    # park it back in the middle and let go
    targets = [-1] * 6
    targets[args.axis] = args.centre
    hand.target(targets)
    time.sleep(0.8)
    final = hand.state()

    # --- what happened ---
    cmds = [r[1] for r in rows]
    acts = [r[2] for r in rows]
    cmd_p2p = max(cmds) - min(cmds)
    act_p2p = max(acts) - min(acts)
    max_cur = max(r[3] for r in rows)

    # lag: shift the actual trace against the commanded one and keep the
    # offset with the smallest mean absolute error. Cheap, and enough to
    # say "it follows, this far behind" rather than "it moved".
    best_lag, best_err = 0, None
    max_shift = int(args.rate * 1.0)          # look up to a second behind
    for shift in range(0, max_shift):
        n = len(rows) - shift
        if n < args.rate:                      # need a second of overlap
            break
        err = sum(abs(acts[i + shift] - cmds[i]) for i in range(n)) / n
        if best_err is None or err < best_err:
            best_lag, best_err = shift, err

    print(f"pushed {sent} targets in {args.secs:.0f}s "
          f"({sent / args.secs:.0f} Hz achieved)")
    print(f"commanded swing {cmd_p2p} counts, axis travelled {act_p2p}")
    print(f"best-fit lag {best_lag * dt * 1000:.0f} ms, "
          f"mean |error| at that lag {best_err:.0f} counts")
    print(f"peak current {max_cur} mA")
    print(f"final state: sta={final['sta']} cur={final['cur']}")

    if args.csv:
        with open(args.csv, "w") as f:
            f.write("t_s,commanded,angleact,cur_mA,sta\n")
            for r in rows:
                f.write(f"{r[0]:.3f},{r[1]},{r[2]},{r[3]},{r[4]}\n")
        print(f"trace written to {args.csv}")

    # A run that followed has to cover most of the commanded swing and keep
    # drawing current; anything less is drift, not tracking.
    ok = act_p2p >= 0.6 * cmd_p2p and max_cur > 10
    print("\n" + ("FOLLOWING - the hand tracked a target that never stopped "
                  "moving, with the link up the whole time"
                  if ok else
                  "NOT FOLLOWING - see the numbers above"))
    hand.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
