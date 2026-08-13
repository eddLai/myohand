#!/usr/bin/env python3
"""How far apart are the two mappings on a real recording?

test_mapping_image.py showed what perspective costs on a synthetic hand with
a noise-free depth channel. The model's own z is not noise-free, and Google
says it carries no consistent scale, so the honest number has to come from
the model's actual output on real frames.

Both mappings run on the same pipeline call, so the only difference is which
of the two landmark sets feeds pose_from_world_landmarks. The disagreement is
reported in counts, the units the driver speaks, against the 15.7 degrees
(135 counts) that made the DPU route unusable.

    python3 compare_image_route.py [frames.npz]
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.expanduser("~/myohand-pipeline/camera"))
import hand_mapping as hm
import hand_pipeline as hp

NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/myohand/nn/refcap/frames.npz")
AXES = ["pinky", "ring", "middle", "index", "thumb", "rot"]

d = np.load(NPZ)
frames = d["frames"]
hands = hp.MediaPipeHands(threads=4)

rows = []
for f in frames:
    r = hands.process(f[:, :, ::-1])
    if not r.multi_hand_world_landmarks:
        continue
    hd = r.multi_handedness[0].classification[0]
    world = r.multi_hand_world_landmarks[0].landmark
    img = r.multi_hand_landmarks[0].landmark
    h, w = f.shape[:2]
    # normalised landmarks are anisotropic; pixels put x and y back on one
    # scale, and z ships at the scale of x already
    px = [SimpleNamespace(x=p.x * w, y=p.y * h, z=p.z * w) for p in img]
    rows.append((hm.pose_from_world_landmarks(world, hd.label),
                 hm.pose_from_world_landmarks(px, hd.label)))

print(f"{len(rows)} frames with a hand, of {len(frames)}\n")
print(f"{'axis':8s} {'median':>8s} {'p90':>8s} {'worst':>8s} {'>135':>7s}")
allbad = 0
for i, name in enumerate(AXES):
    diff = np.abs(np.array([a[i] - b[i] for a, b in rows], dtype=float))
    over = int((diff > 135).sum())
    allbad = max(allbad, over)
    print(f"{name:8s} {np.median(diff):8.0f} {np.percentile(diff, 90):8.0f} "
          f"{diff.max():8.0f} {100 * over / len(diff):6.0f}%")

w_rng = np.array([[a[i] for i in range(6)] for a, _ in rows], dtype=float)
i_rng = np.array([[b[i] for i in range(6)] for _, b in rows], dtype=float)
print(f"\ntravel actually used in this recording, world vs image:")
for i, name in enumerate(AXES):
    print(f"{name:8s} {w_rng[:, i].ptp():8.0f} {i_rng[:, i].ptp():8.0f}")
