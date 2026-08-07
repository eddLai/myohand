"""Does the operator's own hand trip the thumb-rotation interlock?

hand_safety.c parks thumb_rot at ROT_SAFE whenever the index is curled
past ROT_BLOCKED_BELOW, because a curled index sits in the path of the
sweep. teleop draws the target it sends, not the one the driver ends up
executing, so a blocked sweep looks on screen like a sweep that worked.

Replays the recorded poses through the active window and applies the same
rule, per pose, for both calibration profiles -- the finger window moved
too, and a narrower one reads the same finger as more closed.

Reads only.

    ../venv/bin/python3 rot_block_check.py
"""
import collections
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

LM = collections.namedtuple("LM", "x y z")
ROT_BLOCKED_BELOW, ROT_SAFE = 1274, 1466      # hand_safety.c:34-35

PROFILES = ("yuechi-heldpose-20260807", "yuechi-thumb-20260807")
FILES = ("thumb_calib_ui_landmarks.csv", "thumb_steps_landmarks.csv")
KEY = "pose"


def med(v):
    s = sorted(v)
    return s[len(s) // 2] if v else float("nan")


def curl_at(target):
    """The finger bend that lands on a given target, for the window in
    force -- so the threshold can be read in degrees, not counts."""
    n = (target - hm.T_MIN) / float(hm.T_MAX - hm.T_MIN)
    return hm.CURL_CLOSED + n * (hm.CURL_OPEN - hm.CURL_CLOSED)


for prof in PROFILES:
    hm.load_calibration(prof)
    print("\n\n########  profile: %s  ########" % prof)
    print("食指彎過 %.1f° 就會擋住拇指旋轉（target < %d）\n"
          % (curl_at(ROT_BLOCKED_BELOW), ROT_BLOCKED_BELOW))

    for path in FILES:
        p = os.path.join(HERE, path)
        if not os.path.exists(p):
            continue
        rows = [r for r in csv.DictReader(open(p))
                if r["model"] == "lite" and int(r["trust"])]
        if not rows:
            continue
        key = KEY if KEY in rows[0] else "step"
        acc = collections.OrderedDict()
        for r in rows:
            lm = [LM(float(r["x%d" % i]), float(r["y%d" % i]),
                     float(r["z%d" % i])) for i in range(21)]
            t = hm.pose_from_world_landmarks(lm, hm.HANDEDNESS)
            acc.setdefault(r[key], []).append((t[3], t[5]))

        print("  %s" % path)
        print("    %-6s %8s %10s %10s   %s"
              % ("pose", "index", "rot(sent)", "rot(exec)", "結果"))
        for k, v in acc.items():
            idx, rot = med([a for a, _ in v]), med([b for _, b in v])
            blocked = idx < ROT_BLOCKED_BELOW and rot < ROT_SAFE
            print("    %-6s %8d %10d %10d   %s"
                  % (k, idx, rot, ROT_SAFE if blocked else rot,
                     "⛔ 被擋，退回 %d" % ROT_SAFE if blocked else "通過"))
        print()

hm.load_calibration(PROFILES[0])      # leave the active one loaded
