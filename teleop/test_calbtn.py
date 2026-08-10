"""The CALIBRATE button's four outcomes, without a camera or a hand.

run_calibration hands the camera to a child process and takes it back.
Everything that can go wrong there costs the operator the camera if it is
got wrong, so each branch is exercised with the child stubbed out: the
one that saves, the one the tool refuses, the one that crashes, and the
one where the tool is not there at all.

Each branch also has to keep what the child said. The tool prints why it
refused and nothing else records it, so the transcript is checked here
too - including on the branches where there is no tool to print anything.

    ~/myohand/venv/bin/python3 test_calbtn.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

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
    ta.subprocess.Popen = stub
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


REFUSAL = ("  P3 -> P4  opposition     變化   47.2°   "
           "⛔ 汙染，拒絕寫入 — 折拇指的時候不該轉\n")


class Proc:
    """Stands in for the calibration tool: what it printed, then its code.

    Shaped like Popen rather than run() because the reason for a refusal
    has to be read as it is printed - the operator keeps watching the same
    terminal, so the output cannot be swallowed until the child exits.
    """

    def __init__(self, rc, said=REFUSAL):
        self.stdout = io.StringIO(said)
        self._rc = rc

    def wait(self):
        return self._rc


# 1. the tool ran and saved: the profile is there afterwards, so the note
#    names it and the parent has loaded it
saved = {}


def stub_saves(argv, **kw):
    name = [a for a in argv if a.startswith("--save=")][0].split("=", 1)[1]
    saved["name"] = name
    assert "-u" in argv, ("the child buffers into a pipe without -u, and the"
                          " terminal goes quiet for the whole calibration")
    hm.save_calibration({"THUMB_OPEN": 20.0, "THUMB_CLOSED": 90.0},
                        name=name, note="test fixture", path=hm.CAL_PATH)
    return Proc(0, "THUMB_OPEN 18.4 -> 20.0\n")


before = hm.ACTIVE_PROFILE
check("saved -> note names the profile", run_with(stub_saves), "saved as profile")
check("saved -> parent loaded it", hm.ACTIVE_PROFILE or "", saved["name"])
print("  %-42s %s" % ("saved -> sync comes back on",
                      "ok" if ta.auto_sync else "FAIL  auto_sync stayed off"))
if not ta.auto_sync:
    fails.append("sync after save")

# 2. the tool ran and declined to save (a contaminated recording)
check("refused -> note says so", run_with(lambda a, **k: Proc(0)),
      "calibration refused")
# and the sentence saying WHICH pair drifted is kept. Nothing else records
# it: runs/2026-08-10T15-17-05 is a refusal whose reason is gone
check("refused -> the reason it gave is kept", ta.cal_transcript,
      "折拇指的時候不該轉")
check("refused -> transcript carries the reading too", ta.cal_transcript,
      "outcome       calibration refused")
# a refusal must not start the hand moving: nothing was demonstrated, so
# there is nothing the operator has agreed to drive it with
print("  %-42s %s" % ("refused -> sync stays off",
                      "ok" if not ta.auto_sync else "FAIL  auto_sync turned on"))
if ta.auto_sync:
    fails.append("sync after refusal")

# 3. the tool exited nonzero (aborted with q, or crashed)
check("nonzero -> note carries the code", run_with(lambda a, **k: Proc(3)),
      "exit 3")
check("nonzero -> transcript carries it as well", ta.cal_transcript,
      "exit          3")


# 4. the tool could not be launched at all
def stub_missing(argv, **kw):
    raise OSError(2, "No such file or directory")


check("unlaunchable -> note explains", run_with(stub_missing),
      "could not run the calibration tool")
# nothing was printed by a child that never started, so the transcript is
# the header alone - and it has to say that rather than come out empty,
# because an empty one is not written at all
check("unlaunchable -> transcript says it never ran", ta.cal_transcript,
      "exit          not launched")

check("the hand is opened before posing", run_with(lambda a, **k: Proc(0)),
      "calibration refused")
print("  %-42s %s" % ("PARK was the pose sent",
                      "ok" if sent == [ta.PARK] else "FAIL  %r" % sent))
if sent != [ta.PARK]:
    fails.append("PARK sent")

# a sink with no hand behind it must not cost the operator the camera
check("dead sink -> calibration still runs",
      run_with(lambda a, **k: Proc(0), sink=DeadSink()), "calibration refused")

ta.subprocess.Popen = subprocess.Popen

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

# 6. and where that working is kept. The log opened after the calibration
#    is the one whose gains it set, so it is the one that has to carry it.
print("\nthe transcript, next to the log it explains:")


class Log:
    def __init__(self, path):
        self.path = path


tmp = tempfile.mkdtemp(prefix="calbtn-")
try:
    ta.save_transcript(Log(tmp), "outcome       refused\n" + REFUSAL)
    written = os.path.join(tmp, "calibration.txt")
    check("it lands in the run log's own directory",
          "yes" if os.path.exists(written) else "no", "yes")
    check("with what the tool said in it", open(written).read(),
          "折拇指的時候不該轉")

    # --no-log flies without a run log at all, and a calibration during one
    # must not become the thing that ends the session
    ta.save_transcript(None, "outcome       refused\n")
    print("  %-42s ok" % "no run log -> nothing written, no crash")

    # a directory that has gone away is the same kind of event as a run log
    # that could not be opened: reported, not fatal
    shutil.rmtree(tmp)
    ta.save_transcript(Log(tmp), "outcome       refused\n")
    print("  %-42s ok" % "unwritable -> reported, still flying")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

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
