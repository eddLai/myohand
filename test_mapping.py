#!/usr/bin/env python3
"""Does the mapping report the same pose when the hand is rotated?

Builds a synthetic hand at a fixed finger posture, views it from many
orientations, and reports how much each mapping's targets wander. A
mapping that is honest about the pose should not move at all.
"""
import math, statistics
from types import SimpleNamespace

import hand_mapping as hm

PHALANX = {"index": (0.040, 0.025, 0.020), "middle": (0.045, 0.028, 0.021),
           "ring": (0.040, 0.026, 0.020), "pinky": (0.032, 0.020, 0.017)}
MCP_POS = {"index": (0.020, 0.080, 0.0), "middle": (0.005, 0.085, 0.0),
           "ring": (-0.012, 0.082, 0.0), "pinky": (-0.030, 0.072, 0.0)}


def _rot_x(v, deg):
    a = math.radians(deg)
    return (v[0], v[1] * math.cos(a) - v[2] * math.sin(a),
            v[1] * math.sin(a) + v[2] * math.cos(a))


def _rot_y(v, deg):
    a = math.radians(deg)
    return (v[0] * math.cos(a) + v[2] * math.sin(a), v[1],
            -v[0] * math.sin(a) + v[2] * math.cos(a))


def _rot_z(v, deg):
    a = math.radians(deg)
    return (v[0] * math.cos(a) - v[1] * math.sin(a),
            v[0] * math.sin(a) + v[1] * math.cos(a), v[2])


def build_hand(finger_curl_deg, thumb_curl_deg=20.0, thumb_abduct_deg=40.0):
    """Synthesize the 21 landmarks of a hand at the given posture."""
    pts = [(0.0, 0.0, 0.0)] * 21
    # thumb: swing the metacarpal away from the palm by the abduction angle
    a = math.radians(thumb_abduct_deg)
    pts[1] = (0.025, 0.020, 0.0)
    pts[2] = (pts[1][0] + 0.035 * math.cos(a), pts[1][1] + 0.025,
              pts[1][2] - 0.035 * math.sin(a))
    c = math.radians(thumb_curl_deg)
    pts[3] = (pts[2][0] + 0.028 * math.cos(c), pts[2][1] + 0.018,
              pts[2][2] - 0.028 * math.sin(c))
    pts[4] = (pts[3][0] + 0.022 * math.cos(2 * c), pts[3][1] + 0.014,
              pts[3][2] - 0.022 * math.sin(2 * c))
    for name, base in MCP_POS.items():
        mcp_i, pip_i, dip_i, tip_i = hm.FINGER_CHAINS[name]
        l1, l2, l3 = PHALANX[name]
        pts[mcp_i] = base
        # curl the chain into the palm, sharing the bend across the joints
        t1 = finger_curl_deg * 0.5
        t2 = finger_curl_deg
        t3 = finger_curl_deg * 1.3
        p = base
        for length, ang, idx in ((l1, t1, pip_i), (l2, t2, dip_i), (l3, t3, tip_i)):
            d = _rot_x((0.0, length, 0.0), -ang)
            p = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
            pts[idx] = p
    return pts


def view(pts, yaw, pitch, roll):
    out = []
    for p in pts:
        q = _rot_z(_rot_x(_rot_y(p, yaw), pitch), roll)
        out.append(SimpleNamespace(x=q[0], y=q[1], z=q[2]))
    return out


def spread(vals):
    return max(vals) - min(vals)


NAMES = ["pinky", "ring", "middle", "index", "thumb", "rot"]
VIEWS = [(y, p, r) for y in (-40, -20, 0, 20, 40)
                   for p in (-30, 0, 30)
                   for r in (-25, 0, 25)]

print(f"{len(VIEWS)} viewpoints per posture (yaw -40..40, pitch -30..30, roll -25..25)\n")
worst_new = worst_old = 0
for label, curl in (("open hand", 5.0), ("half curl", 35.0), ("fist", 70.0)):
    pts = build_hand(curl)
    new = [hm.pose_from_world_landmarks(view(pts, *v)) for v in VIEWS]
    old = [hm.pose_legacy(view(pts, *v)) for v in VIEWS]
    print(f"--- {label} (finger curl {curl}deg) ---")
    print(f"{'axis':8s} {'joint-angle spread':>20s} {'distance-ratio spread':>23s}")
    for i, n in enumerate(NAMES):
        sn = spread([t[i] for t in new])
        so = spread([t[i] for t in old])
        worst_new, worst_old = max(worst_new, sn), max(worst_old, so)
        print(f"{n:8s} {sn:20d} {so:23d}")
    print()

print(f"worst-case wander over all postures and views:")
print(f"  joint angles on world landmarks : {worst_new} target units")
print(f"  distance ratios on projected xy : {worst_old} target units")
print(f"  (full travel is 1700 units, so {worst_old / 17:.0f}% of range for the old mapping)")
