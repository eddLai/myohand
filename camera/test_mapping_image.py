#!/usr/bin/env python3
"""Would joint angles survive on image landmarks instead of world ones?

test_mapping.py answered a different question. It compared joint angles on
the world skeleton against distance ratios on the projected xy, so its 1700
units belong to the ratios, not to the projection. Two things changed at
once and only their sum was measured.

This isolates the projection. The same synthetic hand is viewed from the
same 45 orientations, and the same joint-angle mapping runs twice: once on
the metric skeleton, once on what a camera would have recorded. Under an
orthographic camera the two differ by a uniform scale, which joint angles
ignore, so every unit of wander printed below comes from perspective alone.
"""
import math
from types import SimpleNamespace

import hand_mapping as hm
from test_mapping import VIEWS, build_hand, spread, view

NAMES = ["pinky", "ring", "middle", "index", "thumb", "rot"]

# a 640-wide sensor at a 60-degree horizontal field of view
FOCAL_PX = 320.0 / math.tan(math.radians(30.0))


def project(pts, distance):
    """What the camera records: xy divided by depth, z at the scale of x.

    MediaPipe defines the image landmarks' z as a depth relative to the
    wrist "at roughly the same scale as x", which is the focal length over
    the working distance. Reproducing that keeps this a test of geometry
    rather than of the model's depth noise.
    """
    zs = [p.z + distance for p in pts]
    if min(zs) <= 0.01:
        raise ValueError("hand is behind or inside the camera")
    wrist_z = zs[0]
    scale = FOCAL_PX / distance
    return [SimpleNamespace(x=FOCAL_PX * p.x / z, y=FOCAL_PX * p.y / z,
                            z=scale * (z - wrist_z))
            for p, z in zip(pts, zs)]


def sweep(distance):
    """Worst-axis wander of each mapping over every viewpoint and posture."""
    rows = []
    for label, curl in (("open hand", 5.0), ("half curl", 35.0), ("fist", 70.0)):
        pts = build_hand(curl)
        views = [view(pts, *v) for v in VIEWS]
        world = [hm.pose_from_world_landmarks(v) for v in views]
        image = [hm.pose_from_world_landmarks(project(v, distance)) for v in views]
        rows.append((label, [spread([t[i] for t in world]) for i in range(6)],
                     [spread([t[i] for t in image]) for i in range(6)]))
    return rows


print(f"{len(VIEWS)} viewpoints per posture, {FOCAL_PX:.0f} px focal length\n")
print("target units of wander; full travel is 1700\n")

for distance in (0.25, 0.40, 0.60):
    print(f"===== hand {distance * 100:.0f} cm from the camera =====")
    print(f"{'posture':12s} {'axis':8s} {'world':>8s} {'image':>8s}")
    worst_w = worst_i = 0
    for label, w, i in sweep(distance):
        for k, n in enumerate(NAMES):
            worst_w, worst_i = max(worst_w, w[k]), max(worst_i, i[k])
            mark = "  <<" if i[k] - w[k] >= 50 else ""
            print(f"{label:12s} {n:8s} {w[k]:8d} {i[k]:8d}{mark}")
    print(f"{'WORST':12s} {'':8s} {worst_w:8d} {worst_i:8d}"
          f"   ({worst_i / 17:.1f}% of travel)\n")
