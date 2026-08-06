#!/usr/bin/env python3
"""Does handd behave, without a hand to try it on?

Starts the daemon against its simulated slave and exercises the parts
that do not depend on the firmware: the socket protocol, the guard every
client has to pass through, the wake sequence, and the disconnect
strategy's down/up cycle. What it cannot test is whether the hand
actually executes anything - that needs the hand.

    python3 test_daemon.py [--daemon ./handd]
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hand_client                                          # noqa: E402
import hand_latency                                         # noqa: E402
import hand_scale                                           # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"{name:<52s} {'ok' if cond else 'FAIL'}{'  ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


def wait_for(predicate, timeout=8.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", default=os.path.join(HERE, "handd"))
    ap.add_argument("--socket", default="/tmp/handd_test.sock")
    ap.add_argument("--latency-log", dest="latency_log",
                    default="/tmp/handd_test_latency.csv")
    args = ap.parse_args()

    if not os.path.exists(args.daemon):
        print(f"skipped: {args.daemon} is not built (C builds on the board, "
              f"not on a Mac)")
        return 0

    # the AL code is the whole answer when a DC run fails, so the daemon has
    # to be able to read one aloud without one having to happen first. It
    # used to blame 0x002d on a switch in the path; the 2026-08-06 direct
    # link reproduced the same code with no switch anywhere, so that
    # reading is now asserted to be gone rather than present.
    al = subprocess.run([args.daemon, "--explain-al=0x002d"],
                        capture_output=True, text=True).stdout
    check("AL 0x002d is explained, and not blamed on a switch",
          "No Sync Error" in al and "switch" not in al.lower(), al.strip())

    # and an unknown trigger must never fall back to the default
    bad = subprocess.run([args.daemon, "--trigger=nonsense"],
                         capture_output=True, text=True)
    check("an unknown trigger is refused rather than defaulted",
          bad.returncode != 0 and "unknown trigger" in bad.stderr)

    if os.path.exists(args.latency_log):
        os.unlink(args.latency_log)
    log = open("/tmp/handd_test.log", "w+")
    proc = subprocess.Popen(
        [args.daemon, "--simulate", f"--socket={args.socket}",
         "--hold-ms=100", "--settle-ms=200",
         f"--latency-log={args.latency_log}"],
        stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_for(lambda: os.path.exists(args.socket), timeout=10):
            print("FAIL: the daemon never opened its socket")
            log.seek(0)
            print(log.read())
            return 1

        hand = hand_client.HandClient(path=args.socket).connect()

        info = hand.hello()
        check("hello names the trigger it was started with",
              info["trigger"] == "disconnect", str(info))
        check("hello admits it is simulated", info["simulate"] is True)
        check("the daemon's scale matches hand_scale.py",
              info["scale"] == hand_scale.as_dict())

        dc = hand.command("dc")
        check("dc reports the fields the decisive DC run turns on",
              all(k in dc for k in ("hasdc", "configdc", "dcactive", "pdelay",
                                    "al", "al_reading", "delta_ns")), str(dc))

        st = hand.state()
        check("state reports six axes of telemetry",
              all(len(st[k]) == 6 for k in ("pos", "ang", "cur", "sta", "tmp")))
        check("the wake sequence cleared standby before serving clients",
              7 not in st["sta"], str(st.get("sta")))

        # the guard has to act on a pose that closes index and thumb together
        r = hand.target([0, 0, 0, 0, 0, 1500])
        check("a clashing pose is clamped by the daemon, not the client",
              r["guarded"] >= 1 and "thumb_bend" in r["guard_note"], str(r))
        r = hand.target([2000, 2000, 2000, 2000, 2000, 2000])
        check("an open hand passes the guard untouched", r["guarded"] == 0, str(r))

        # out-of-range clamps rather than dropping the frame
        r = hand.target([9999, -400, -1, 1000, 1500, 1500])
        check("out-of-range targets are accepted and clamped", r["ok"] is True)

        # A streaming client keeps pushing, which is the whole point of the
        # daemon - and with the disconnect trigger it has to, because a
        # reconnect parks every axis back on "hold" rather than re-asserting
        # the last pose. So drive it the way teleop will.
        opened = saw_down = saw_up = queued = False
        end = time.time() + 10
        while time.time() < end and not (opened and saw_down and saw_up and queued):
            r = hand.target([2000] * 6)
            queued = queued or bool(r.get("queued"))
            st = hand.state()
            if st.get("bus") == "down":
                saw_down = True
            else:
                saw_up = saw_up and True or saw_down
                if min(st.get("ang", [0])) > 1500:
                    opened = True
            time.sleep(0.01)
        check("commanded axes move toward the target", opened)
        check("the disconnect trigger really drops the link", saw_down)
        check("the link comes back by itself", saw_up)
        check("a target arriving mid-disconnect is queued, not dropped", queued)

        # the latency ruler has to produce the same columns either way, and
        # it has to survive a client that sends no stamps at all
        stamps = hand_latency.Stamps()
        for pose in ([300] * 4 + [700, 1500], [2000] * 6,
                     [400] * 4 + [700, 1500], [1900] * 6):
            stamps.frame()
            time.sleep(0.03)                      # stand in for the camera
            stamps.mapped()
            hand.target(pose, stamps)
            time.sleep(1.2)                       # let the step play out
        stats = hand.stats()
        check("stats reports a breakdown, not just a total",
              stats["samples"] > 0 and set(hand_latency.STAGES) <=
              set(stats["p50"]) | {"total"}, str(stats))
        check("the client-side stages actually arrived",
              stats["p50"]["vision"] >= 25000, str(stats["p50"]))
        check("motion onset was timed", stats["p50"]["move"] >= 0,
              str(stats["p50"]))
        check("the disconnect trigger's execution stage is timed too",
              stats["p50"]["exec"] >= 0, str(stats["p50"]))

        jit = stats["cycle_late_us"]
        check("cycle jitter is measured, not assumed",
              jit["samples"] > 100 and jit["p50"] >= 0 and
              jit["max"] >= jit["p95"] >= jit["p50"], str(jit))
        check("the loop keeps its period to well inside one cycle",
              jit["p95"] < 1000000 // 1000, f"p95={jit['p95']}us of a 1000us cycle")

        rows = hand_latency.read_csv(args.latency_log)
        stamped = [r for r in rows if r["vision_us"] > 0]
        check("the latency log is written and parses",
              len(rows) >= 1 and rows[0]["trigger"] == "disconnect",
              str(rows[:1]))
        check("every stamped step is recorded, not just the first",
              len(stamped) >= 4, f"{len(stamped)} of 4 stamped steps logged")
        check("stages that do not apply are -1, not zero",
              all(isinstance(r["vision_us"], int) for r in rows))

        # a second client can attach without disturbing the first
        other = hand_client.HandClient(path=args.socket).connect()
        check("a second client is served too", other.state()["ok"] is True)
        other.close()
        check("the first client survives the second leaving",
              hand.state()["ok"] is True)

        # unknown commands are refused rather than guessed at
        try:
            hand.command("wiggle")
            check("an unknown command is refused", False)
        except hand_client.HandDaemonError as e:
            check("an unknown command is refused", "unknown command" in str(e))
        try:
            hand.command("target 1 2 3")
            check("a short target line is refused", False)
        except hand_client.HandDaemonError as e:
            check("a short target line is refused", "6 values" in str(e))

        hand.close()

        # and it shuts down on a signal without leaving its socket behind
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        check("SIGTERM shuts the daemon down cleanly", proc.returncode == 0,
              f"returncode={proc.returncode}")
        check("the socket file is removed on exit",
              not os.path.exists(args.socket))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        log.seek(0)
        text = log.read()
        log.close()
        if fails:
            print("\n--- daemon log ---\n" + text)

    print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
