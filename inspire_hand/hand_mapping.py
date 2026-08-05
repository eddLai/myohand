"""Map a MediaPipe hand skeleton onto RH56F1 joint targets.

The first teleop version scored flexion as a distance ratio over image
landmarks (tip-to-wrist divided by mcp-to-wrist). That measure moves
when the hand rotates in front of the camera even though the fingers
have not moved, because projected distances shorten with viewing angle.

This version reads MediaPipe's world landmarks - metric 3D coordinates
in a wrist-centred frame - and scores flexion as the actual joint
angles. An angle between two bones is invariant to how the hand is
rotated or how far away it sits, so the same fist reports the same
targets from any viewpoint.
"""
import json
import math
import os
import time

import hand_scale

# landmark ids: wrist 0, thumb 1-4, index 5-8, middle 9-12, ring 13-16, pinky 17-20
FINGER_CHAINS = {          # (mcp, pip, dip, tip)
    "pinky":  (17, 18, 19, 20),
    "ring":   (13, 14, 15, 16),
    "middle": (9, 10, 11, 12),
    "index":  (5, 6, 7, 8),
}
THUMB_CHAIN = (1, 2, 3, 4)  # cmc, mcp, ip, tip

CURL_OPEN, CURL_CLOSED = 15.0, 150.0     # total curl in degrees
THUMB_OPEN, THUMB_CLOSED = 15.0, 110.0
ABD_MIN, ABD_MAX = 10.0, 50.0            # thumb abduction from the palm plane; see calibrate.py

# Robot targets. T_MAX is the top of the scale; T_MIN sits above its
# bottom on purpose - a teleop source should never command a full crush.
T_MIN, T_MAX = 300, hand_scale.TARGET_MAX
ROT_MIN = 700

# The scale itself lives in hand_scale (mirroring hand_safety.h). Nothing
# here recomputes it, so the open question about whether targets really
# run 0..2000 is answered in one file.
target_from_angle = hand_scale.ang_to_target


CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")

#: which window names a profile is allowed to set
WINDOW_KEYS = ("CURL_OPEN", "CURL_CLOSED", "THUMB_OPEN", "THUMB_CLOSED",
               "ABD_MIN", "ABD_MAX")


def raw_features(lm):
    """The angles the windows above are calibrated against."""
    curls = [finger_curl(lm, c) for c in FINGER_CHAINS.values()]
    return {"curl_lo": min(curls), "curl_hi": max(curls),
            "thumb": _joint_angle(lm, 1, 2, 3) + _joint_angle(lm, 2, 3, 4),
            "abd": thumb_abduction(lm)}


# ---- calibration profiles ------------------------------------------------
#
# Calibration windows are measured data, not settings. The file used to be
# one flat set of numbers, which meant the CALIBRATE button overwrote it -
# and the numbers in it came from 245 measured frames, so a stray click on
# a badly lit day cost real work.
#
# So the file holds named profiles and one `active` pointer. Saving never
# overwrites an existing name: a new calibration lands under a new name
# and becomes active, leaving the old one to go back to.


def _empty_file():
    return {"active": None, "profiles": {}}


def _read_file(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return _empty_file()
    if "profiles" in data:
        return data
    # the old flat format: keep it, under a name, rather than dropping it
    windows = {k: v for k, v in data.items() if k in WINDOW_KEYS}
    if not windows:
        return _empty_file()
    return {"active": "legacy",
            "profiles": {"legacy": {
                "note": "migrated from the old single-set calibration.json",
                "windows": windows}}}


def list_profiles(path=CAL_PATH):
    data = _read_file(path)
    return data["active"], data["profiles"]


def load_calibration(name=None, path=CAL_PATH):
    """Apply a profile's windows. Defaults to the active one.

    Returns the windows applied, or None when there is nothing to apply -
    the module defaults then stand, which is what a fresh clone gets.
    """
    data = _read_file(path)
    name = name or data.get("active")
    profile = data["profiles"].get(name) if name else None
    if not profile:
        return None
    windows = {k: v for k, v in profile.get("windows", {}).items()
               if k in WINDOW_KEYS}
    globals().update(windows)
    globals()["ACTIVE_PROFILE"] = name
    return windows


def save_calibration(windows, name=None, note="", path=CAL_PATH,
                     overwrite=False):
    """Store a freshly measured set of windows under its own name.

    Generates a timestamped name when none is given, so the button in
    teleop cannot land on top of an existing profile by accident.
    """
    data = _read_file(path)
    windows = {k: v for k, v in windows.items() if k in WINDOW_KEYS}
    name = name or time.strftime("session-%Y%m%d-%H%M%S")
    if name in data["profiles"] and not overwrite:
        raise ValueError(
            f"profile '{name}' already exists - pick another name, or pass "
            f"overwrite=True if you really mean to replace measured data")
    data["profiles"][name] = {
        "note": note or "captured from the teleop CALIBRATE button",
        "measured": time.strftime("%Y-%m-%d"),
        "windows": windows,
    }
    data["active"] = name
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    globals().update(windows)
    globals()["ACTIVE_PROFILE"] = name
    return name


def _sub(a, b):
    return (a.x - b.x, a.y - b.y, a.z - b.z)


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _norm(u):
    return math.sqrt(_dot(u, u))


def _angle(u, v):
    """Angle between two vectors, in degrees."""
    d = _norm(u) * _norm(v)
    if d < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v) / d))))


def _joint_angle(lm, a, b, c):
    """Bend at joint b, 0 when the two bones are straight."""
    return 180.0 - _angle(_sub(lm[a], lm[b]), _sub(lm[c], lm[b]))


def _scale(value, lo, hi, out_lo, out_hi):
    n = (value - lo) / (hi - lo)
    n = max(0.0, min(1.0, n))
    return int(out_lo + n * (out_hi - out_lo))


def finger_curl(lm, chain):
    """Total curl of one finger: the bend at MCP plus the bend at PIP."""
    mcp, pip, dip, _tip = chain
    wrist = 0
    return _joint_angle(lm, wrist, mcp, pip) + _joint_angle(lm, mcp, pip, dip)


def thumb_abduction(lm):
    """Angle between the thumb metacarpal and the palm plane."""
    palm_normal = _cross(_sub(lm[5], lm[0]), _sub(lm[17], lm[0]))
    thumb_bone = _sub(lm[2], lm[1])
    return abs(90.0 - _angle(thumb_bone, palm_normal))


def pose_from_world_landmarks(lm):
    """Return [pinky, ring, middle, index, thumb_bend, thumb_rot] targets.

    Targets follow the robot convention: 0 closed, 2000 open.
    """
    tgt = []
    for name in ("pinky", "ring", "middle", "index"):
        curl = finger_curl(lm, FINGER_CHAINS[name])
        tgt.append(_scale(curl, CURL_CLOSED, CURL_OPEN, T_MIN, T_MAX))
    cmc, mcp, ip, _tip = THUMB_CHAIN
    thumb_curl = _joint_angle(lm, cmc, mcp, ip) + _joint_angle(lm, mcp, ip, 4)
    tgt.append(_scale(thumb_curl, THUMB_CLOSED, THUMB_OPEN, T_MIN, T_MAX))
    tgt.append(_scale(thumb_abduction(lm), ABD_MIN, ABD_MAX, ROT_MIN, T_MAX))
    return tgt


# --- the original image-space mapping, kept so the two can be compared ---
FINGERS_LEGACY = {"pinky": (17, 20), "ring": (13, 16), "middle": (9, 12), "index": (5, 8)}
R_MIN, R_MAX = 1.05, 1.85
TH_MIN = 500


def pose_legacy(lm):
    """Distance-ratio mapping over projected (x, y) - view dependent."""
    def d2(a, b):
        return math.hypot(a.x - b.x, a.y - b.y)
    w = lm[0]
    tgt = []
    for name in ("pinky", "ring", "middle", "index"):
        mcp, tip = FINGERS_LEGACY[name]
        r = d2(lm[tip], w) / max(d2(lm[mcp], w), 1e-6)
        tgt.append(_scale(r, R_MIN, R_MAX, T_MIN, T_MAX))
    r = d2(lm[4], w) / max(d2(lm[2], w), 1e-6)
    tgt.append(_scale(r, 1.10, 1.50, TH_MIN, T_MAX))
    r2 = d2(lm[4], lm[5]) / max(d2(lm[0], lm[5]), 1e-6)
    tgt.append(_scale(r2, 0.30, 0.75, ROT_MIN, T_MAX))
    return tgt


ACTIVE_PROFILE = None
load_calibration()


if __name__ == "__main__":
    active, profiles = list_profiles()
    if not profiles:
        print(f"no profiles in {CAL_PATH}; the module defaults are in use")
    for name, p in profiles.items():
        mark = "*" if name == active else " "
        print(f"{mark} {name:28s} {p.get('measured', '?'):12s} {p.get('note', '')}")
        for k, v in p.get("windows", {}).items():
            print(f"    {k:14s} {v}")
    print("\n* = active. Keys a profile does not set keep the module default.")
