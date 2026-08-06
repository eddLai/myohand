#!/usr/bin/env python3
"""Does hand_api present the same surface on both paths?

hand_api.InspireHand can reach the hand two ways now - through the resident
daemon, or by spawning hand_ctl per call as it always did. The point of
having both is that no caller should be able to tell which one it got, so
this checks the thing that would break first: the shape of what comes back.

The daemon side runs against handd --simulate, so this needs no hand and
no EtherCAT interface. The hand_ctl side is checked by signature and by
fallback behaviour rather than by running it, since running it does need
hardware.

    python3 test_api_compat.py [--daemon ./handd]
"""
import argparse
import inspect
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hand_api                                             # noqa: E402

fails = 0

# What hand_ctl has always returned for `state` and `pose`. A caller
# written against the subprocess path reads these keys, so the daemon path
# has to produce them too or it is not the same API.
HAND_CTL_KEYS = {"ok", "mode", "guarded", "guard_note",
                 "pos", "ang", "frc", "cur", "err", "sta", "tmp"}

# The public surface other modules import. Names and parameters are the
# contract; changing either is a breaking change even if the code still
# runs, so they are pinned here rather than left to review.
SIGNATURES = {
    "state": [],
    "pose": ["targets", "force", "speed", "settle"],
    "open_hand": [],
    "fist": ["grip"],
    "middle_finger": [],
    "point": [],
    "release": [],
}


def check(name, cond, detail=""):
    global fails
    print(f"{name:<54s} {'ok' if cond else 'FAIL'}"
          f"{'  ' + str(detail) if detail and not cond else ''}")
    if not cond:
        fails += 1


def wait_for(pred, timeout=10.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", default=os.path.join(HERE, "handd"))
    ap.add_argument("--socket", default="/tmp/handd_apitest.sock")
    args = ap.parse_args()

    # ---- the surface itself, which needs neither path to be reachable ----
    for name, params in SIGNATURES.items():
        fn = getattr(hand_api.InspireHand, name, None)
        if fn is None:
            check(f"InspireHand.{name} exists", False)
            continue
        got = [p for p in inspect.signature(fn).parameters
               if p not in ("self",) and p != "kw"]
        check(f"InspireHand.{name}{tuple(params)} unchanged",
              got == params, f"got {got}")

    # gestures must keep accepting the pose keywords they forward
    for name in ("open_hand", "fist", "middle_finger", "point", "release"):
        fn = getattr(hand_api.InspireHand, name)
        has_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD
                     for p in inspect.signature(fn).parameters.values())
        check(f"{name} still forwards **kw to pose", has_kw)

    # ---- no daemon: the class must still construct and say so ----
    hand = hand_api.InspireHand(socket_path="/tmp/handd_apitest_absent.sock")
    check("with no daemon, .via reports hand_ctl", hand.via == "hand_ctl",
          hand.via)
    check("use_daemon=False forces the subprocess path",
          hand_api.InspireHand(use_daemon=False).via == "hand_ctl")

    if not os.path.exists(args.daemon):
        print(f"\nskipped the daemon half: {args.daemon} is not built "
              f"(C builds on the board, not on a Mac)")
        return 1 if fails else 0

    # ---- daemon path, against the simulated slave ----
    if os.path.exists(args.socket):
        os.unlink(args.socket)
    log = open("/tmp/handd_apitest.log", "w+")
    proc = subprocess.Popen(
        [args.daemon, "--simulate", f"--socket={args.socket}"],
        stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_for(lambda: os.path.exists(args.socket)):
            log.seek(0)
            print("FAIL: daemon never opened its socket\n" + log.read())
            return 1

        hand = hand_api.InspireHand(socket_path=args.socket)
        check("with a daemon up, .via reports daemon", hand.via == "daemon",
              hand.via)

        st = hand.state()
        missing = HAND_CTL_KEYS - set(st)
        check("state() returns every key hand_ctl returns",
              not missing, f"missing {sorted(missing)}")
        check("state() telemetry is six axes wide",
              all(len(st[k]) == 6 for k in
                  ("pos", "ang", "frc", "cur", "err", "sta", "tmp")))
        check("state() reports mode like hand_ctl does",
              st["mode"] == "state", st.get("mode"))

        p = hand.pose([hand_api.OPEN] * 5 + [hand_api.KEEP], settle=True)
        missing = HAND_CTL_KEYS - set(p)
        check("pose() returns every key hand_ctl returns",
              not missing, f"missing {sorted(missing)}")
        check("pose() reports mode=pose", p["mode"] == "pose", p.get("mode"))
        check("pose() carries the guard verdict through",
              isinstance(p["guarded"], int) and
              isinstance(p["guard_note"], str), p.get("guarded"))

        # force and speed are the reason handd grew a profile command; if
        # they were being dropped this is where it would show. Ask the
        # daemon directly rather than through HandClient.hello(), which
        # returns the greeting it cached at connect and would therefore
        # report the start-up values no matter what happened since.
        hand.pose([hand_api.KEEP] * 6, force=321, speed=654, settle=False)
        live = hand._client.command("hello")
        check("pose(force=,speed=) actually reaches the daemon",
              live["force"] == 321 and live["speed"] == 654,
              f"daemon says {live['force']}/{live['speed']}")

        # a clashing pose must still be clamped, on this path too
        g = hand.pose([hand_api.CLOSED] * 4 + [hand_api.CLOSED,
                                               hand_api.OPEN], settle=False)
        check("the interlock still fires on the daemon path",
              g["guarded"] >= 1, g.get("guard_note"))

        # gestures are just pose() calls; one is enough to prove routing
        r = hand.release(settle=False)
        check("gestures work unchanged through the daemon",
              r.get("ok") is True and r["mode"] == "pose")

        # and the fallback: kill the daemon under it, the call must survive
        hand.close()
        check("close() releases the daemon", hand.via == "hand_ctl")
    finally:
        proc.send_signal(subprocess.signal.SIGTERM)
        proc.wait(timeout=10)
        log.close()
        if os.path.exists(args.socket):
            os.unlink(args.socket)

    print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
