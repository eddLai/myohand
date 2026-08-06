#!/usr/bin/env python3
"""Does every entry point let go of what it holds when interrupted?

This exists because it did not. teleop_app.py handled no signals and
released the camera only after its main loop, so Ctrl+C left a process
holding /dev/video0 and the next run blocked on opening it. Nothing caught
that, because every test here ran to completion and completion was the one
path that worked.

So each check starts a real process, interrupts it the way a terminal
would, and then asks the operating system whether the thing it held is
free. Not whether it printed something reassuring on the way out.

Worth being precise about what needs guaranteeing. File descriptors -
cameras, sockets, flock - the kernel reclaims when a process dies, even on
SIGKILL, so those need no help. What needs it is that the process actually
dies when asked, and the two things the kernel will not undo: a child it
spawned, and a hand left mid-motion.

    python3 test_release.py

Needs no hand: the daemon runs --simulate and the sinks are exercised
without one. Skips the camera check if there is no camera.
"""
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"{name:<56s} {'ok' if cond else 'FAIL'}"
          f"{'  ' + str(detail) if detail and not cond else ''}")
    if not cond:
        fails += 1


def wait_gone(proc, timeout=10.0):
    """Wait for a child to exit, and reap it.

    Not os.kill(pid, 0): these are our own children, so an exited one is a
    zombie until it is waited for, and signal 0 succeeds on a zombie. The
    first version of this test reported three processes as still running
    when all three had exited and released everything they held."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def camera_held():
    """Who has /dev/video0, by asking the kernel rather than by guessing."""
    try:
        out = subprocess.run(["fuser", "/dev/video0"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return bool(out)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None                          # cannot tell; do not pretend


def port_held(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def interrupt(proc, sig=signal.SIGINT):
    """Signal the whole group, which is what a terminal does on Ctrl+C."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except OSError:
        proc.send_signal(sig)


# ---- teleop: the one that actually broke ---------------------------------
def test_teleop_releases_camera():
    if not os.path.exists("/dev/video0"):
        print("teleop camera check                                       "
              "skipped (no /dev/video0)")
        return
    if camera_held():
        print("teleop camera check                                       "
              "skipped (something else already holds the camera)")
        return
    py = os.environ.get("TELEOP_PYTHON", sys.executable)
    try:
        subprocess.run([py, "-c", "import cv2, mediapipe"], check=True,
                       capture_output=True, timeout=60)
    except Exception:                                       # noqa: BLE001
        print("teleop camera check                                       "
              "skipped (this interpreter has no cv2/mediapipe)")
        return

    proc = subprocess.Popen(
        # teleop_app.py lives in teleop/, not here, since the restructure
        [py, os.path.normpath(os.path.join(HERE, "..", "teleop",
                                           "teleop_app.py")),
         "--sink=none", "--headless", "--device=0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        end = time.time() + 20
        while time.time() < end and not camera_held():
            if proc.poll() is not None:
                check("teleop opened the camera", False, "it exited early")
                return
            time.sleep(0.2)
        check("teleop opened the camera", camera_held() is True)

        interrupt(proc)
        check("teleop exits on SIGINT", wait_gone(proc, 15))
        time.sleep(0.5)
        check("the camera is free afterwards", camera_held() is False,
              "still held")
    finally:
        if proc.poll() is None:
            interrupt(proc, signal.SIGKILL)
            proc.wait(timeout=5)


# ---- the daemon ----------------------------------------------------------
def test_handd_releases_socket():
    handd = os.path.join(HERE, "handd")
    if not os.path.exists(handd):
        print("handd socket check                                        "
              "skipped (not built)")
        return
    sock = "/tmp/handd_release_test.sock"
    if os.path.exists(sock):
        os.unlink(sock)
    proc = subprocess.Popen([handd, "--simulate", f"--socket={sock}"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        end = time.time() + 15
        while time.time() < end and not os.path.exists(sock):
            time.sleep(0.05)
        check("handd opened its socket", os.path.exists(sock))

        interrupt(proc)
        check("handd exits on SIGINT", wait_gone(proc, 15))
        check("the socket file is gone", not os.path.exists(sock))
    finally:
        if proc.poll() is None:
            interrupt(proc, signal.SIGKILL)
            proc.wait(timeout=5)
        if os.path.exists(sock):
            os.unlink(sock)


# ---- the sink that spawns children --------------------------------------
def test_handset_sink_stops_its_child():
    """close() has to stop the hand_set it spawned.

    The worker thread is a daemon and dies with the process, but the child
    does not - and until it finishes it holds the bus lock, so the next
    master reports the bus busy with nothing visibly holding it."""
    import hand_sink

    stub = "/tmp/handset_stub_release_test.sh"
    with open(stub, "w") as f:
        f.write("#!/bin/sh\nsleep 60\n")
    os.chmod(stub, 0o755)
    try:
        sink = hand_sink.HandSetSink(binary=stub)
        sink.send([1200] * 6)
        end = time.time() + 5
        while time.time() < end and sink._proc is None:
            time.sleep(0.05)
        child = sink._proc
        check("the sink spawned a child", child is not None)
        if child is None:
            return
        sink.close()
        check("close() stops the child it spawned",
              child.poll() is not None, "still running")
    finally:
        os.unlink(stub)


# ---- the HTTP server -----------------------------------------------------
def test_hand_server_releases_port():
    server = os.path.join(HERE, "hand_server.py")
    if not os.path.exists(server):
        print("hand_server port check                                    "
              "skipped (not present)")
        return
    if port_held(8100):
        print("hand_server port check                                    "
              "skipped (8100 already in use)")
        return
    proc = subprocess.Popen([sys.executable, server],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        end = time.time() + 15
        while time.time() < end and not port_held(8100):
            if proc.poll() is not None:
                print("hand_server port check                            "
                      "        skipped (it could not start)")
                return
            time.sleep(0.1)
        if not port_held(8100):
            print("hand_server port check                                "
                  "    skipped (never bound 8100)")
            return
        check("hand_server bound its port", True)

        interrupt(proc)
        check("hand_server exits on SIGINT", wait_gone(proc, 15))
        time.sleep(0.3)
        check("port 8100 is free afterwards", not port_held(8100))
    finally:
        if proc.poll() is None:
            interrupt(proc, signal.SIGKILL)
            proc.wait(timeout=5)


def main():
    test_teleop_releases_camera()
    test_handd_releases_socket()
    test_handset_sink_stops_its_child()
    test_hand_server_releases_port()
    print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
