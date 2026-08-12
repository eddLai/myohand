"""Work out what the model's handedness scalar means, rather than assuming.

thumb_trust rejects a frame outright when the label disagrees with the operator's
hand, and rejects it again when the score falls below 0.85, so a wrapper that
reports handedness the wrong way round would either freeze the thumb constantly
or stop guarding it at all. MediaPipe applies its own convention on top of the
model - which side counts as left depends on whether the image was mirrored -
and the recording holds both the raw frames and the labels MediaPipe gave them,
so the convention can be measured instead of guessed.

    python3 handedness_map.py [refcap/frames.npz]
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hand_pipeline import HandPipeline

NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/myohand/nn/refcap/frames.npz")
d = np.load(NPZ)
frames, found, label, score = d["frames"], d["found"], d["label"], d["score"]

pipe = HandPipeline()
rows = []
for i, bgr in enumerate(frames):
    out = pipe(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if out is None or not found[i]:
        continue
    rows.append((str(label[i]), float(score[i]), out["handedness"]))

if not rows:
    sys.exit("沒有可比對的幀")
labels = sorted({r[0] for r in rows})
print("MediaPipe 給的標籤：%s（%d 幀）\n" % (labels, len(rows)))
for L in labels:
    sel = [r for r in rows if r[0] == L]
    raw = np.array([r[2] for r in sel])
    mp_s = np.array([r[1] for r in sel])
    print("%-6s %3d 幀   模型純量 中位 %.3f  範圍 %.3f-%.3f"
          % (L, len(sel), np.median(raw), raw.min(), raw.max()))
    print("            MediaPipe 分數 中位 %.3f  範圍 %.3f-%.3f"
          % (np.median(mp_s), mp_s.min(), mp_s.max()))
    # if the scalar is the probability of this label, the two agree directly;
    # if it is the probability of the other one, they agree after inversion
    print("            |純量 - 分數| 中位 %.3f    |1-純量 - 分數| 中位 %.3f"
          % (np.median(np.abs(raw - mp_s)), np.median(np.abs(1 - raw - mp_s))))
