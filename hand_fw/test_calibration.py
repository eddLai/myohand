#!/usr/bin/env python3
"""Can a stray CALIBRATE click still destroy measured data?

The windows in calibration.json came from 245 measured frames. They used
to live in a flat file that the teleop button overwrote, which meant one
click on a badly lit day cost all of it. These checks are the reason that
cannot happen now.

    python3 test_calibration.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# hand_mapping lives in camera/ since the restructure, and this path is
# anchored to this file rather than the cwd.
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "camera")))

import hand_mapping as hm                                   # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"{name:<52s} {'ok' if cond else 'FAIL'}"
          f"{'  ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


# --- what a profile may set -----------------------------------------------
# There is no shipped profile to assert: camera/calibration.json is
# gitignored and per-operator. This block used to require the one committed
# at c429524, whose measured windows were thumb *abduction* - and abduction
# stopped driving the target when the mapping moved to opposition, so those
# numbers cannot stand in for a measured OPP window. Re-measuring is a job
# for whoever is next in front of the camera.
check("every window a profile may set is one the module applies",
      all(hasattr(hm, k) for k in hm.WINDOW_KEYS), str(hm.WINDOW_KEYS))

before = (hm.CURL_OPEN, hm.CURL_CLOSED)
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "calibration.json")
    hm.save_calibration({"THUMB_OPEN": 21.0}, name="partial", path=path)
    hm.load_calibration("partial", path=path)
    check("a profile that omits a window leaves that window alone",
          (hm.CURL_OPEN, hm.CURL_CLOSED) == before,
          f"{(hm.CURL_OPEN, hm.CURL_CLOSED)} != {before}")

# --- saving cannot land on top of measured data ---------------------------
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "calibration.json")
    with open(path, "w") as f:
        json.dump({"active": "measured",
                   "profiles": {"measured": {
                       "note": "precious", "measured": "2026-08-02",
                       "windows": {"THUMB_OPEN": 24.0}}}}, f)

    name = hm.save_calibration({"THUMB_OPEN": 99.0}, path=path)
    _, after = hm.list_profiles(path)
    check("a save lands under a new name", name != "measured", name)
    check("the measured profile is untouched",
          after["measured"]["windows"]["THUMB_OPEN"] == 24.0, str(after))
    check("the new one becomes active", hm.list_profiles(path)[0] == name)

    try:
        hm.save_calibration({"THUMB_OPEN": 1.0}, name="measured", path=path)
        check("overwriting a name by accident is refused", False)
    except ValueError as e:
        check("overwriting a name by accident is refused",
              "already exists" in str(e))
    hm.save_calibration({"THUMB_OPEN": 1.0}, name="measured", path=path,
                        overwrite=True)
    check("overwriting on purpose still works",
          hm.list_profiles(path)[1]["measured"]["windows"]["THUMB_OPEN"] == 1.0)

    # switching back has to actually restore the numbers
    hm.load_calibration(name, path=path)
    check("loading a named profile applies its windows", hm.THUMB_OPEN == 99.0)
    check("asking for a profile that is not there returns None, quietly",
          hm.load_calibration("no-such-profile", path=path) is None)

# --- the old flat file is migrated, not discarded -------------------------
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "calibration.json")
    with open(path, "w") as f:
        json.dump({"THUMB_OPEN": 21.0, "ABD_MAX": 33.0}, f)
    got = hm.load_calibration(path=path)
    check("an old flat calibration.json is still readable",
          got == {"THUMB_OPEN": 21.0}, str(got))
    # ABD_MAX is exactly the legacy key this filter exists for: the next
    # line of load_calibration is globals().update(windows), so a window
    # the module no longer has must be dropped rather than injected.
    check("a window the module no longer knows is dropped, not injected",
          not hasattr(hm, "ABD_MAX"))
    check("it arrives under a name rather than nameless",
          hm.list_profiles(path)[0] == "legacy")

# leave the module as the repo found it, so import order cannot matter
hm.load_calibration()
print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
sys.exit(1 if fails else 0)
