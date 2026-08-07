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


def _unit(v):
    m = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / m, v[1] / m, v[2] / m)


def _crossv(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _walk(p, d, length):
    return (p[0] + length * d[0], p[1] + length * d[1], p[2] + length * d[2])


def build_hand(finger_curl_deg, thumb_curl_deg=20.0, thumb_opp_deg=30.0,
               thumb_abd_deg=55.0):
    """Synthesize the 21 landmarks of a hand at the given posture.

    The thumb metacarpal is laid out in the same palm frame the mapping
    derives (a right hand as the mirrored teleop pipeline sees it, palm
    facing -z), so thumb_opp_deg and thumb_abd_deg are exact ground truth.
    """
    pts = [(0.0, 0.0, 0.0)] * 21
    pa = _unit(MCP_POS["index"])         # wrist sits at the origin
    n = (0.0, 0.0, -1.0)                 # palm-ward
    e1 = _unit(_crossv(n, pa))           # in the palm plane, thumb-ward
    al, ph = math.radians(thumb_abd_deg), math.radians(thumb_opp_deg)
    t = tuple(math.cos(al) * pa[i]
              + math.sin(al) * (math.cos(ph) * e1[i] + math.sin(ph) * n[i])
              for i in range(3))
    pts[1] = (0.025, 0.020, 0.0)
    pts[2] = _walk(pts[1], t, 0.035)
    # distal bones bend in the plane of t and the palm normal, so the
    # flexion plane swings with opposition and the bends sum to the curl
    u = _unit(tuple(n[i] - sum(n[j] * t[j] for j in range(3)) * t[i]
                    for i in range(3)))
    c = math.radians(thumb_curl_deg / 2)
    d1 = tuple(math.cos(c) * t[i] + math.sin(c) * u[i] for i in range(3))
    d2 = tuple(math.cos(2 * c) * t[i] + math.sin(2 * c) * u[i] for i in range(3))
    pts[3] = _walk(pts[2], d1, 0.028)
    pts[4] = _walk(pts[3], d2, 0.022)
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


# --- extremes: direction is a property of the mapping, reach is calibration ---
def sweep(param, values):
    out = []
    for v in values:
        pts = build_hand(20.0, **{param: v})
        out.append(hm.pose_from_world_landmarks(view(pts, 0, 0, 0)))
    return out


print("\n--- thumb at its extremes ---")
bend = [t[4] for t in sweep("thumb_curl_deg", range(0, 101, 10))]
rot = [t[5] for t in sweep("thumb_opp_deg", range(0, 91, 10))]
fail = 0
for name, series, rising in (("bend vs curl", bend, False),
                             ("rot vs opposition", rot, False)):
    ok = all((b >= a) == rising or a == b for a, b in zip(series, series[1:]))
    print(f"{name:18s} monotonic {'up' if rising else 'down'}: {'ok' if ok else 'FAIL'}"
          f"   reaches {min(series)}..{max(series)}")
    fail += not ok
print(f"unreachable head-room: bend {hm.T_MAX - max(bend)} units, "
      f"rot {hm.T_MAX - max(rot)} units (ground truth is exact, expect 0)")

print("\n--- channel independence ---")
rot_flex = [t[5] for t in sweep("thumb_curl_deg", range(0, 101, 10))]
bend_opp = [t[4] for t in sweep("thumb_opp_deg", range(0, 81, 10))]
rot_curl = [hm.pose_from_world_landmarks(view(build_hand(c), 0, 0, 0))[5]
            for c in range(0, 71, 10)]
for name, series in (("rot while thumb flexes", rot_flex),
                     ("bend while thumb opposes", bend_opp),
                     ("rot while fingers curl", rot_curl)):
    s = spread(series)
    print(f"{name:26s} spread {s:4d} units  {'ok' if s == 0 else 'FAIL'}")
    fail += s != 0

print("\n--- opposition ground truth under rotation ---")
worst = 0.0
cases = 0
for phi in (0, 20, 40, 60, 80):
    for alpha in (35, 55, 75):
        pts = build_hand(20.0, thumb_opp_deg=phi, thumb_abd_deg=alpha)
        for v in VIEWS:
            got = hm.thumb_features(view(pts, *v))["opposition"]
            worst = max(worst, abs(got - phi))
            cases += 1
print(f"worst |measured - true| over {cases} cases: {worst:.3f} deg  "
      f"{'ok' if worst < 0.5 else 'FAIL'}")
fail += worst >= 0.5

print("\n--- mirrored left hand agrees ---")
pts = view(build_hand(30.0, thumb_curl_deg=40.0, thumb_opp_deg=50.0), 15, -10, 5)
right = hm.pose_from_world_landmarks(pts)
left = hm.pose_from_world_landmarks(
    [SimpleNamespace(x=-p.x, y=p.y, z=p.z) for p in pts], handedness="Left")
diff = max(abs(a - b) for a, b in zip(right, left))
print(f"right {right} vs mirrored-left {left}: "
      f"{'ok' if diff <= 1 else 'FAIL'}")
fail += diff > 1

def to_image(pts):
    """Project to image convention (x right, y down, camera on -z): a
    180-deg camera roll about z puts the synthetic thumb where the
    mirrored teleop frame shows it (image-left on a palm view)."""
    return [SimpleNamespace(x=-p.x, y=-p.y, z=p.z) for p in pts]


print("\n--- facing: silhouette sign vs true palm normal ---")
pts0 = build_hand(20.0)
bad = 0
for v in [(y, p, r) for y in (0, 40, 140, 180, 220)
                    for p in (0, 25) for r in (0, 30)]:
    nz = _rot_z(_rot_x(_rot_y((0.0, 0.0, -1.0), v[0]), v[1]), v[2])[2]
    want = "palm" if nz < 0 else "back"
    img = to_image(view(pts0, *v))
    bad += hm.hand_facing(img, "Right") != want
    bad += hm.hand_facing(img, "Left") == want   # chirality flips the sign
edge = hm.hand_facing(to_image(view(pts0, 90, 0, 0)), "Right")
print(f"20 views x2 chirality: {'ok' if bad == 0 else f'{bad} FAIL'};"
      f" edge-on reads {edge}  {'ok' if edge is None else 'FAIL'}")
fail += bad + (edge is not None)

print("\n--- thumb occlusion and the trust gate ---")
crossed = build_hand(20.0, thumb_opp_deg=130.0, thumb_abd_deg=55.0)
splayed = build_hand(20.0, thumb_opp_deg=10.0, thumb_abd_deg=55.0)
front = lambda p: to_image(view(p, 0, 0, 0))
back = lambda p: to_image(view(p, 180, 0, 0))
for got, want, name in (
        (hm.thumb_occluded(back(crossed), "back"), True, "crossed thumb hidden from the back"),
        (hm.thumb_occluded(back(splayed), "back"), False, "splayed thumb visible from the back"),
        (hm.thumb_occluded(front(crossed), "palm"), False, "crossed thumb visible from the palm"),
        (hm.thumb_trust(front(pts0), "Right", 0.99, "Right")[0], True, "clean palm view trusted"),
        (hm.thumb_trust(front(pts0), "Left", 0.99, "Right")[0], False, "flipped label distrusted"),
        (hm.thumb_trust(front(pts0), "Right", 0.6, "Right")[0], False, "unsure label distrusted"),
        (hm.thumb_trust(to_image(view(pts0, 90, 0, 0)), "Right", 0.99, "Right")[0],
         False, "edge-on distrusted"),
        (hm.thumb_trust(back(crossed), "Right", 0.99, "Right")[0],
         False, "hidden thumb distrusted")):
    print(f"{name:38s} {'ok' if got == want else 'FAIL'}")
    fail += got != want

if fail:
    print("DIRECTION FAILURE")


# --- per-axis travel: the thumb axes do not span the finger scale --------
#
# Measured on the hand 2026-08-07 from eight teleop run logs. thumb_bend
# reaches 1375 at the open end (commanded above 1500 and held ten seconds,
# it sat at exactly 1375 drawing 0 mA) and 1123 at the closed end, where
# commanding 1034 pinned it and ramped the current to 1397 mA. Before this
# was measured the axis was driven over the fingers' full 816 counts, so
# three quarters of the command range was outside the mechanism and the
# bottom of it was stalling the motor.
#
# Asserted here rather than trusted, because the failure is silent: a
# mapping that commands past a stop looks fine in every plot of the command
# and only shows up as heat in the actuator.

BEND_STOP_CLOSED, BEND_STOP_OPEN = 1123, 1375
bad = 0
for flexion in [hm.THUMB_OPEN - 20, hm.THUMB_OPEN, 40.0, 70.0,
                hm.THUMB_CLOSED, hm.THUMB_CLOSED + 20]:
    v = hm._scale(flexion, hm.THUMB_CLOSED, hm.THUMB_OPEN,
                  hm.BEND_MIN, hm.BEND_MAX)
    if not BEND_STOP_CLOSED <= v <= BEND_STOP_OPEN:
        print(f"thumb_bend flexion {flexion:.0f} deg -> {v}, outside "
              f"{BEND_STOP_CLOSED}..{BEND_STOP_OPEN}  FAIL")
        bad += 1
print(f"thumb_bend stays inside its measured travel "
      f"({hm.BEND_MIN}..{hm.BEND_MAX} of {BEND_STOP_CLOSED}..{BEND_STOP_OPEN})"
      f"  {'ok' if bad == 0 else 'FAIL'}")
fail += bad

clear = hm.BEND_MIN - BEND_STOP_CLOSED
print(f"{'closed stop kept clear by ' + str(clear) + ' counts':38s} "
      f"{'ok' if clear > 0 else 'FAIL - commands into the stall'}")
fail += clear <= 0

span = hm.BEND_MAX - hm.BEND_MIN
finger_span = hm.T_MAX - hm.T_MIN
print(f"{'thumb_bend span ' + str(span) + ' vs fingers ' + str(finger_span):38s} "
      f"{'ok' if span < finger_span // 2 else 'FAIL - back on the full scale'}")
fail += span >= finger_span // 2
