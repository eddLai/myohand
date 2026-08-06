#!/usr/bin/env python3
"""Does the continuous-following path work, with no camera and no hand?

teleop itself needs a webcam and a display, so it cannot run here. What
can run is everything underneath it: the sink that decides where a pose
goes, the rate at which it goes there, and the deadband that stops an
unchanged pose being resent. Those are the parts that turn "one gesture
every two to three seconds" into following.

    python3 test_teleop_sink.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hand_latency                                         # noqa: E402
import hand_sink                                            # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"{name:<52s} {'ok' if cond else 'FAIL'}"
          f"{'  ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


def main():
    daemon = os.path.join(HERE, "handd")
    sock = "/tmp/handd_sink_test.sock"

    # --- the parts that need nothing at all -----------------------------
    dry = hand_sink.open_sink("none")
    dry.send([1000] * 6)
    check("the dry-run sink accepts poses and keeps the last one",
          dry.last_target == [1000] * 6 and dry.sent == 1)
    check("dry run turns the settle gate off",
          hand_sink.settle_frames_default("none") == 0)
    check("hand_set keeps the gate, because a pose there costs seconds",
          hand_sink.settle_frames_default("hand_set") == 5)
    check("streaming turns the gate off, which is what following means",
          hand_sink.settle_frames_default("daemon") == 0)
    try:
        hand_sink.open_sink("wishful")
        check("an unknown sink is refused", False)
    except ValueError as e:
        check("an unknown sink is refused", "unknown sink" in str(e))

    # asking for the daemon when there is none must fail loudly rather than
    # quietly becoming a different measurement
    try:
        hand_sink.open_sink("daemon", socket_path="/tmp/definitely-not-here.sock")
        check("a missing daemon is an error, not a silent downgrade", False)
    except Exception as e:                                   # noqa: BLE001
        check("a missing daemon is an error, not a silent downgrade",
              "handd" in str(e), str(e))

    if not os.path.exists(daemon):
        print(f"\nskipped the streaming checks: {daemon} is not built "
              f"(C builds on the board, not on a Mac)")
        print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
        return 1 if fails else 0

    # --- streaming into a real (simulated) daemon ------------------------
    log = open("/tmp/handd_sink_test.log", "w+")
    proc = subprocess.Popen(
        [daemon, "--simulate", f"--socket={sock}", "--hold-ms=100",
         "--settle-ms=200"], stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(500):
            if os.path.exists(sock):
                break
            time.sleep(0.02)

        sink = hand_sink.open_sink("daemon", socket_path=sock, rate_hz=50)
        check("the daemon sink connects and reports the trigger",
              sink.info.get("trigger") == "disconnect", str(sink.info))
        check("it says out loud that it is simulated",
              sink.simulated and "SIMULATED" in sink.last_result)

        # sweep a pose the way a hand in front of a camera would
        stamps = hand_latency.Stamps()
        for step in range(60):
            stamps.frame()
            v = 1082 + step * 12          # target counts, ANGLEACT scale
            stamps.mapped()
            sink.send([v, v, v, v, 1322, 1610], stamps)
            time.sleep(0.02)
        time.sleep(0.5)
        check("a moving pose is streamed, not sent once", sink.sent >= 10,
              f"sent={sink.sent}")

        # and an unchanging pose stops costing anything
        before = sink.sent
        for _ in range(50):
            sink.send([1802, 1802, 1802, 1802, 1322, 1610])
            time.sleep(0.02)
        time.sleep(0.3)
        settled = sink.sent - before
        check("an unchanged pose is not resent every tick", settled <= 3,
              f"{settled} sends for a still hand")

        check("telemetry comes back for the gauge to draw",
              sink.actual is not None and len(sink.actual) == 6,
              str(sink.actual))

        stats = sink.stats()
        check("the stream is measured by the same ruler as everything else",
              stats["samples"] > 0 and stats["p50"]["vision"] >= 0, str(stats))

        sink.close()
        check("closing the sink stops the pump", not sink._thread.is_alive())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.seek(0)
        text = log.read()
        log.close()
        if fails:
            print("\n--- daemon log ---\n" + text)

    print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
