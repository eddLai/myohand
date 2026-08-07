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
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "hand_fw")))
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
OPP_MIN, OPP_MAX = 10.0, 90.0            # thumb opposition sweep; see calibrate.py

HANDEDNESS = "Right"       # the operator's hand; calibration locks it in
FACING_MARGIN = 0.10       # silhouette area below this fraction is edge-on
LABEL_SURE = 0.85          # handedness score under this means mid-flip

# Robot targets, in ANGLEACT counts (see hand_scale). T_MAX is the top of
# the scale; T_MIN sits above its bottom on purpose - a teleop source
# should never command a full crush. Both moved with the scale correction
# of 2026-08-06: T_MIN was 300 on the 0..2000 scale this file used to
# assume, carried to the position it named (890 + 300*960/2000).
T_MIN, T_MAX = 1034, hand_scale.TARGET_MAX
# ROT_MIN was 700 pre-correction and is carried the same way (1226), which
# errs toward leaving the thumb further clear. A 2026-08-05 on-hand test
# (see git history) measured the palm-ward hard stop directly at ANGLEACT
# 600 and back-converted it to "target ~300" on the old scale - that is a
# second, more direct measurement of the same stop that predates the
# scale correction and has not been reconciled with it. Whoever can stand
# next to the hand again should decide between 1226 (safe, arithmetic)
# and something nearer 600 (measured, but on an axis whose own travel
# limits are not otherwise characterised - see hand_fw/hand_safety.h).
ROT_MIN = 1226

# Thumb-bend has its own travel too, and it is nothing like the global
# scale. MEASURED ON THE HAND, 2026-08-07, from eight teleop run logs
# (runs/*_open_and_close and siblings; every frame carries ANGLEACT and
# current, see filter/run_log.py):
#
#   open end   1375. Commanded above 1500 and HELD FOR TEN SECONDS, ANGLEACT
#              stayed at exactly 1375 and drew 0 mA the whole time. Not
#              slowness - the axis is capped at speed 500 by hs_profile, but
#              ten seconds is many times its full travel, and a motor that
#              is still trying does not draw nothing.
#   closed end 1123. It reaches this and stops; commanding 1042 pinned it
#              there and ramped the current 109 -> 1397 mA, held for over
#              0.4 s. That is the stall hand_safety.h:93-96 warns cooks the
#              actuator, and it happened because this file was asking for
#              1034.
#
# So the usable travel is 252 counts, not the 816 the axis was being driven
# over: three quarters of the command range fell outside the mechanism and
# the bottom of it was doing damage. BEND_MIN keeps 17 counts clear of the
# closed stop for the same reason T_MIN sits above the scale's bottom.
# BEND_MAX is the measured stop exactly, because resting against the open
# end costs nothing - it is where the axis idles anyway.
#
# This is the per-axis sweep hand_safety.h:63-68 says has not been done. It
# still has not been done properly: these are the limits the operator's own
# gestures happened to reach, so they are a lower bound on travel, and only
# for this unit. A deliberate sweep would settle them.
BEND_MIN, BEND_MAX = 1140, 1375

# The scale itself lives in hand_scale (mirroring hand_safety.h). Nothing
# here recomputes it, so what a target number means is answered in one
# file.
target_from_angle = hand_scale.ang_to_target


CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")

#: which window names a profile is allowed to set
WINDOW_KEYS = ("CURL_OPEN", "CURL_CLOSED", "THUMB_OPEN", "THUMB_CLOSED",
               "OPP_MIN", "OPP_MAX", "HANDEDNESS")


def raw_features(lm, handedness=None):
    """The angles the windows above are calibrated against."""
    curls = [finger_curl(lm, c) for c in FINGER_CHAINS.values()]
    tf = thumb_features(lm, handedness)
    return {"curl_lo": min(curls), "curl_hi": max(curls),
            "thumb": tf["flexion"], "opp": tf["opposition"]}


# ---- calibration profiles ------------------------------------------------
#
# Calibration windows are measured data, not settings. The file used to be
# one flat set of numbers, which meant the CALIBRATE button overwrote it -
# and the numbers in it came from real recorded frames, so a stray click on
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


def _palm_frame(lm, handedness="Right"):
    """Orthonormal palm frame: a = wrist->index-MCP, n palm-ward, e1 radial
    in-plane toward the thumb. The teleop pipeline mirrors the frame before
    MediaPipe, so a physical right hand arrives with left-handed chirality;
    the MediaPipe handedness label tracks that chirality either way."""
    a = _sub(lm[5], lm[0])
    n = _cross(a, _sub(lm[17], lm[0]))
    e1 = _cross(a, n)          # a x (a x pinky): thumb-ward for either chirality
    if handedness == "Right":
        n = (-n[0], -n[1], -n[2])
    out = []
    for v in (a, n, e1):
        m = _norm(v)
        if m < 1e-9:
            return None
        out.append((v[0] / m, v[1] / m, v[2] / m))
    return out


def thumb_features(lm, handedness=None):
    """The thumb split into the three motions the CMC + MCP can make, degrees.

    opposition: rotation of the metacarpal about the wrist->index-MCP axis;
                0 splayed in the palm plane, ~90 swept across the palm.
    abduction:  splay of the metacarpal away from the index metacarpal.
    flexion:    MCP + IP bend.

    The metacarpal is one unit vector, so (opposition, abduction) exhaust its
    freedom; CMC flexion has no third number of its own, it lives inside these
    two. Downstream learners should treat this dict as the separated channels.
    """
    if handedness is None:
        handedness = HANDEDNESS
    flex = _joint_angle(lm, 1, 2, 3) + _joint_angle(lm, 2, 3, 4)
    frame = _palm_frame(lm, handedness)
    t = _sub(lm[2], lm[1])
    m = _norm(t)
    if frame is None or m < 1e-9:
        return {"flexion": flex, "abduction": 0.0, "opposition": 0.0}
    a, n, e1 = frame
    t = (t[0] / m, t[1] / m, t[2] / m)
    y, x = _dot(t, n), _dot(t, e1)
    opp = math.degrees(math.atan2(y, x)) if math.hypot(y, x) > 1e-6 else 0.0
    return {"flexion": flex, "abduction": _angle(t, a), "opposition": opp}


def hand_facing(lm, handedness=None):
    """'palm' or 'back' toward the camera, None near edge-on.

    Works on image landmarks (x right, y down): the signed area of
    wrist->index-MCP x wrist->pinky-MCP flips with facing and with
    chirality, so the operator's locked handedness picks the sign. The
    2D silhouette is the part MediaPipe gets right even when its depth
    is hallucinated, which is what makes this signal worth trusting.
    """
    if handedness is None:
        handedness = HANDEDNESS
    ux, uy = lm[5].x - lm[0].x, lm[5].y - lm[0].y
    vx, vy = lm[17].x - lm[0].x, lm[17].y - lm[0].y
    s = ux * vy - uy * vx
    span = math.hypot(ux, uy) * math.hypot(vx, vy)
    if span < 1e-9 or abs(s) < FACING_MARGIN * span:
        return None
    if handedness == "Left":
        s = -s
    return "palm" if s > 0 else "back"


def _inside2d(p, poly):
    """Even-odd point-in-polygon over image (x, y)."""
    inside = False
    for i in range(len(poly)):
        a, b = poly[i], poly[i - 1]
        if (a.y > p.y) != (b.y > p.y):
            if p.x < a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y):
                inside = not inside
    return inside


def thumb_occluded(lm, facing):
    """True when the palm stands between the camera and the thumb: the
    back of the hand faces us and the distal thumb projects inside the
    palm outline, so whatever MediaPipe drew there is imagination."""
    if facing != "back":
        return False
    palm = [lm[i] for i in (0, 5, 9, 13, 17)]
    return _inside2d(lm[3], palm) and _inside2d(lm[4], palm)


def thumb_trust(lm, label, score, handedness=None):
    """Should this frame's thumb decomposition be believed?

    MediaPipe draws plausible thumbs it cannot see, so the gates ask
    whether it could have seen this one: a handedness label disagreeing
    with the operator's locked hand means the net currently perceives
    the mirror hand, whose depth relief flips the opposition sign; a low
    label score means it is mid-flip; edge-on to the palm plane the
    silhouette carries no facing at all; and from the back of the hand a
    thumb drawn inside the palm outline is drawn from imagination.
    Returns (trust, why) with why one of "", "hand looks flipped",
    "handedness unsure", "edge-on", "thumb hidden".
    """
    if handedness is None:
        handedness = HANDEDNESS
    if label != handedness:
        return False, "hand looks flipped"
    if score < LABEL_SURE:
        return False, "handedness unsure"
    facing = hand_facing(lm, handedness)
    if facing is None:
        return False, "edge-on"
    if thumb_occluded(lm, facing):
        return False, "thumb hidden"
    return True, ""


def pose_from_world_landmarks(lm, handedness=None):
    """Return [pinky, ring, middle, index, thumb_bend, thumb_rot] targets.

    Targets are ANGLEACT counts: ~890 closed, ~1850 open. thumb_rot is
    driven by the opposition angle alone: swept across the palm -> ROT_MIN
    (the palm-ward hard stop), reposed in the palm plane -> open.

    Only the four fingers span the whole scale. The two thumb axes have
    their own travel and are mapped onto it - thumb_rot onto ROT_MIN..T_MAX
    and thumb_bend onto BEND_MIN..BEND_MAX, which is 252 counts against the
    fingers' 816. Driving an axis over a range it does not have does not
    produce a bigger gesture; it produces a stall at the end of it.
    """
    tgt = []
    for name in ("pinky", "ring", "middle", "index"):
        curl = finger_curl(lm, FINGER_CHAINS[name])
        tgt.append(_scale(curl, CURL_CLOSED, CURL_OPEN, T_MIN, T_MAX))
    tf = thumb_features(lm, handedness)
    tgt.append(_scale(tf["flexion"], THUMB_CLOSED, THUMB_OPEN,
                      BEND_MIN, BEND_MAX))
    tgt.append(_scale(tf["opposition"], OPP_MAX, OPP_MIN, ROT_MIN, T_MAX))
    return tgt


# --- the original image-space mapping, kept so the two can be compared ---
FINGERS_LEGACY = {"pinky": (17, 20), "ring": (13, 16), "middle": (9, 12), "index": (5, 8)}
R_MIN, R_MAX = 1.05, 1.85
TH_MIN = 1130          # was 500 before the 2026-08-06 scale correction


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
