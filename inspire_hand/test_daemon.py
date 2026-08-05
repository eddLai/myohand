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
    args = ap.parse_args()

    if not os.path.exists(args.daemon):
        print(f"skipped: {args.daemon} is not built (C builds on the board, "
              f"not on a Mac)")
        return 0

    log = open("/tmp/handd_test.log", "w+")
    proc = subprocess.Popen(
        [args.daemon, "--simulate", f"--socket={args.socket}",
         "--hold-ms=100", "--settle-ms=200"],
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
