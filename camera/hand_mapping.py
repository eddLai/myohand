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

T_MIN, T_MAX = 300, 2000                 # robot targets; never command a full crush
ROT_MIN = 300   # 2026-08-05 KD240 實測：掌心方向硬止點 ANG_ACT 600，對應目標 ~300

ANG_CLOSED, ANG_OPEN = 890, 1850         # ANGLEACT span measured on this unit


def target_from_angle(ang):
    """Where a joint actually sits, on the same scale the targets use."""
    span = (int(ang) - ANG_CLOSED) * 2000 / (ANG_OPEN - ANG_CLOSED)
    return max(0, min(2000, int(span)))


CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")


def raw_features(lm, handedness=None):
    """The angles the windows above are calibrated against."""
    curls = [finger_curl(lm, c) for c in FINGER_CHAINS.values()]
    tf = thumb_features(lm, handedness)
    return {"curl_lo": min(curls), "curl_hi": max(curls),
            "thumb": tf["flexion"], "opp": tf["opposition"]}


def load_calibration(path=CAL_PATH):
    """Prefer windows measured from a real hand over the defaults."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    globals().update({k: v for k, v in data.items() if k in globals()})
    return data


def save_calibration(data, path=CAL_PATH):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    globals().update({k: v for k, v in data.items() if k in globals()})


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

    Targets follow the robot convention: 0 closed, 2000 open. thumb_rot is
    driven by the opposition angle alone: swept across the palm -> ROT_MIN
    (the palm-ward hard stop), reposed in the palm plane -> open.
    """
    tgt = []
    for name in ("pinky", "ring", "middle", "index"):
        curl = finger_curl(lm, FINGER_CHAINS[name])
        tgt.append(_scale(curl, CURL_CLOSED, CURL_OPEN, T_MIN, T_MAX))
    tf = thumb_features(lm, handedness)
    tgt.append(_scale(tf["flexion"], THUMB_CLOSED, THUMB_OPEN, T_MIN, T_MAX))
    tgt.append(_scale(tf["opposition"], OPP_MAX, OPP_MIN, ROT_MIN, T_MAX))
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


load_calibration()
