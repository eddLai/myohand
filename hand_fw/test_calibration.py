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


# --- the shipped asset ----------------------------------------------------
active, profiles = hm.list_profiles()
check("the repo ships the measured profile",
      "operator-2026-08-02" in profiles, str(list(profiles)))
w = profiles.get("operator-2026-08-02", {}).get("windows", {})
check("its measured values are the ones from the 245-frame run",
      w == {"THUMB_OPEN": 24.0, "THUMB_CLOSED": 80.0,
            "ABD_MIN": 10.0, "ABD_MAX": 30.0}, str(w))
check("it is the active profile", active == "operator-2026-08-02", str(active))
check("importing hand_mapping applies it",
      (hm.THUMB_OPEN, hm.THUMB_CLOSED, hm.ABD_MIN, hm.ABD_MAX)
      == (24.0, 80.0, 10.0, 30.0))
check("windows the profile omits keep the module default",
      hm.CURL_OPEN == 15.0 and hm.CURL_CLOSED == 150.0)

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
          got == {"THUMB_OPEN": 21.0, "ABD_MAX": 33.0}, str(got))
    check("it arrives under a name rather than nameless",
          hm.list_profiles(path)[0] == "legacy")

# leave the module as the repo found it, so import order cannot matter
hm.load_calibration()
print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
sys.exit(1 if fails else 0)
