"""Save the crops the landmark model actually receives, to calibrate against.

Post-training quantisation picks each layer's scale from the range of values it
sees, so the images fed to it have to be the images the model will meet in
service. Raw camera frames are not those: the model never sees one. It sees a
square cut out of the frame, rotated so the hand stands upright and resized to
224, and the statistics of that differ from a wide-angle room in every way that
matters - a hand fills the frame, the background is mostly gone, and the
orientation is fixed.

Until the pipeline existed there was no way to produce those crops outside
MediaPipe. Now they fall out of the tracking loop, so they are written here in
the exact form the interpreter is handed.

    python3 make_calib.py [out.npy] [refcap/frames.npz]
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hand_pipeline import HandPipeline

OUT = sys.argv[1] if len(sys.argv) > 1 else "calib_crops.npy"
NPZ = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
    "~/myohand/nn/refcap/frames.npz")

d = np.load(NPZ)
frames = d["frames"]
pipe = HandPipeline()

crops = []
for bgr in frames:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    # reach into the loop rather than re-deriving the crop, so what is saved is
    # byte for byte what the interpreter was given
    seen = {}
    orig = pipe.lm.interp_landmark.set_tensor

    def spy(idx, val, _o=orig, _s=seen):
        _s["x"] = np.array(val, copy=True)
        return _o(idx, val)

    pipe.lm.interp_landmark.set_tensor = spy
    out = pipe(rgb)
    pipe.lm.interp_landmark.set_tensor = orig
    if "x" in seen:
        crops.append(seen["x"][0])

if not crops:
    sys.exit("沒有產生任何裁切圖")

arr = np.stack(crops).astype(np.float32)
np.save(OUT, arr)
print("%d 張 %s  值域 %.3f 到 %.3f  平均 %.3f"
      % (len(arr), "x".join(map(str, arr.shape[1:])), arr.min(), arr.max(),
         arr.mean()))
print("寫到 %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1e6))
if len(arr) < 100:
    print("⚠ 少於 100 張，Vitis AI 建議 100-1000")
