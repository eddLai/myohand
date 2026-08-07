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


opened = []


def open_camera(device):
    opened.append(device)
    return Cap()


def run_with(stub):
    del opened[:]
    ta.subprocess.run = stub
    cap = Cap()
    back = ta.run_calibration(0, open_camera, cap)
    assert cap.released, "the old capture was not released"
    assert back is not None and opened == [0], "the camera was not reopened"
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

# 2. the tool ran and declined to save (a contaminated recording)
check("refused -> note says so", run_with(lambda a, **k: R(0)),
      "calibration refused")

# 3. the tool exited nonzero (aborted with q, or crashed)
check("nonzero -> note carries the code", run_with(lambda a, **k: R(3)),
      "exit 3")


# 4. the tool could not be launched at all
def stub_missing(argv, **kw):
    raise OSError(2, "No such file or directory")


check("unlaunchable -> note explains", run_with(stub_missing),
      "could not run the calibration tool")

ta.subprocess.run = subprocess.run

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
