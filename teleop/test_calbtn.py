"""The CALIBRATE button's four outcomes, without a camera or a hand.

run_calibration hands the camera to a child process and takes it back.
Everything that can go wrong there costs the operator the camera if it is
got wrong, so each branch is exercised with the child stubbed out: the
one that saves, the one the tool refuses, the one that crashes, and the
one where the tool is not there at all.

    ~/myohand/venv/bin/python3 test_calbtn.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "camera"))
sys.path.insert(0, os.path.join(HERE, os.pardir, "hand_fw"))

import teleop_app as ta  # noqa: E402
import hand_mapping as hm  # noqa: E402

fails = []


def check(name, got, want):
    ok = want in got
    print("  %-42s %s" % (name, "ok" if ok else "FAIL  got %r" % got))
    if not ok:
        fails.append(name)


class Cap:
    """Stands in for the VideoCapture, and remembers being let go."""

    def __init__(self):
        self.released = False

    def release(self):
        self.released = True

    def isOpened(self):     # noqa: N802 - matching cv2's name, not ours
        return True


sent = []


class Sink:
    """Records the poses run_calibration hands it."""

    name, busy, deadband = "stub", False, 0

    def send(self, tgt, stamps=None):
        sent.append(list(tgt))


class DeadSink:
    """A sink that has lost the hand. Parking is a courtesy; a calibration
    must still happen, and the camera must still come back."""

    name, busy, deadband = "dead", False, 0

    def send(self, tgt, stamps=None):
        raise RuntimeError("no handd on /tmp/inspire_hand.sock")


opened = []


def open_camera(device):
    opened.append(device)
    return Cap()


def run_with(stub, sink=None):
    del opened[:], sent[:]
    ta.subprocess.run = stub
    ta.sink = sink or Sink()
    ta.last_sent = [1] * 6            # stale, as it is after a calibration
    ta.auto_sync = False              # the operator has not clicked SYNC
    cap = Cap()
    back = ta.run_calibration(0, open_camera, cap)
    assert cap.released, "the old capture was not released"
    assert back is not None and opened == [0], "the camera was not reopened"
    assert ta.last_sent is None, "a stale target survived; the deadband will "                                 "swallow the first pose after calibrating"
    return ta.cal_note


print("CAL_TOOL exists:", os.path.exists(ta.CAL_TOOL), "->", ta.CAL_TOOL)
if not os.path.exists(ta.CAL_TOOL):
    fails.append("CAL_TOOL path")

print("\nthe four outcomes:")


class R:
    def __init__(self, rc):
        self.returncode = rc


# 1. the tool ran and saved: the profile is there afterwards, so the note
#    names it and the parent has loaded it
saved = {}


def stub_saves(argv, **kw):
    name = [a for a in argv if a.startswith("--save=")][0].split("=", 1)[1]
    saved["name"] = name
    hm.save_calibration({"THUMB_OPEN": 20.0, "THUMB_CLOSED": 90.0},
                        name=name, note="test fixture", path=hm.CAL_PATH)
    return R(0)


before = hm.ACTIVE_PROFILE
check("saved -> note names the profile", run_with(stub_saves), "saved as profile")
check("saved -> parent loaded it", hm.ACTIVE_PROFILE or "", saved["name"])
print("  %-42s %s" % ("saved -> sync comes back on",
                      "ok" if ta.auto_sync else "FAIL  auto_sync stayed off"))
if not ta.auto_sync:
    fails.append("sync after save")

# 2. the tool ran and declined to save (a contaminated recording)
check("refused -> note says so", run_with(lambda a, **k: R(0)),
      "calibration refused")
# a refusal must not start the hand moving: nothing was demonstrated, so
# there is nothing the operator has agreed to drive it with
print("  %-42s %s" % ("refused -> sync stays off",
                      "ok" if not ta.auto_sync else "FAIL  auto_sync turned on"))
if ta.auto_sync:
    fails.append("sync after refusal")

# 3. the tool exited nonzero (aborted with q, or crashed)
check("nonzero -> note carries the code", run_with(lambda a, **k: R(3)),
      "exit 3")


# 4. the tool could not be launched at all
def stub_missing(argv, **kw):
    raise OSError(2, "No such file or directory")


check("unlaunchable -> note explains", run_with(stub_missing),
      "could not run the calibration tool")

check("the hand is opened before posing", run_with(lambda a, **k: R(0)),
      "calibration refused")
print("  %-42s %s" % ("PARK was the pose sent",
                      "ok" if sent == [ta.PARK] else "FAIL  %r" % sent))
if sent != [ta.PARK]:
    fails.append("PARK sent")

# a sink with no hand behind it must not cost the operator the camera
check("dead sink -> calibration still runs",
      run_with(lambda a, **k: R(0), sink=DeadSink()), "calibration refused")

ta.subprocess.run = subprocess.run

# 5. what the run log opened after a calibration records about it. The cut
#    happens either way - the camera was gone and the filter is rebuilt - so
#    the log is the only place that can say whether anything changed. Before
#    this, a refused calibration produced a before/after pair that read as a
#    real one: runs/2026-08-10T15-15-22 and 15-17-05 carry the same profile
#    and the same six gains to the last digit.
check("refused -> the next log says the profile held",
      ta.cut_reason("session-A", "session-A"),
      "calibration refused - profile unchanged")
check("saved -> the next log names the profile it moved to",
      ta.cut_reason("session-A", "session-B"), "calibration -> session-B")
check("saved from module defaults -> still names it",
      ta.cut_reason(None, "session-B"), "calibration -> session-B")
check("refused from module defaults -> still reads as unchanged",
      ta.cut_reason(None, None), "calibration refused - profile unchanged")

# clean up after ourselves: the fixture profile is not measured data
import json  # noqa: E402

d = json.load(open(hm.CAL_PATH))
d["profiles"].pop(saved["name"], None)
d["active"] = before
json.dump(d, open(hm.CAL_PATH, "w"), indent=2)
open(hm.CAL_PATH, "a").write("\n")
print("\nremoved fixture profile %s; active back to %s"
      % (saved["name"], json.load(open(hm.CAL_PATH))["active"]))

print("\n%s" % ("all ok" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
