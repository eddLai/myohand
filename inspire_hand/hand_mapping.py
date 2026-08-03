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
import math

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

T_MIN, T_MAX = 300, 2000                 # robot targets; never command a full crush
ROT_MIN = 700


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
