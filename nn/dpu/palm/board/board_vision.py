"""The whole vision path on the board: camera, DPU palm, A53 landmark.

This is the arrangement the hand would be driven from, measured end to end
rather than model by model, because the per-model numbers (30 fps on the DPU,
27.7 on the A53) leave out the crop, the letterbox and the copies between
them, and those are what decide whether the loop keeps up with the camera.

The camera is opened as MJPG on purpose. USB 2.0 cannot carry uncompressed
720p at frame rate and silently settles at 4.4 fps if asked to.

    python board_vision.py [seconds] [--tflite]

--tflite runs the stock palm model on the CPU instead, for comparison.
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/pipe_bench"))

# pynq_dpu and ai_edge_litert live in different environments and only the
# first one can be the interpreter. The litert tree is appended rather than
# inserted so that its numpy, built against a different ABI, never shadows
# the one cv2 and vart are already using here.
sys.path.append(os.path.expanduser(
    "~/rh56f1_kd240/env/lib/python3.10/site-packages"))

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 20.0
USE_DPU = "--tflite" not in sys.argv

MODELS = os.path.expanduser("~/rh56f1_kd240/models")
PALM = os.path.join(MODELS, "palm_detection_lite.tflite")
LAND = os.path.join(MODELS, "hand_landmark_lite.tflite")

import hand_pipeline as hp

pipe = hp.HandPipeline(palm=PALM, landmark=LAND, threads=4)
if USE_DPU:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dpu_palm import DpuPalm
    dpu = DpuPalm()
    dpu.attach(pipe.det)
    print("palm: DPU, threshold %.2f" % pipe.det.min_score_thresh)
else:
    print("palm: tflite on A53, threshold %.2f" % pipe.det.min_score_thresh)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_FPS, 30)
for _ in range(5):
    cap.read()

frames = held = 0
t0 = time.time()
last = None
while time.time() - t0 < SECONDS:
    ok, bgr = cap.read()
    if not ok:
        break
    frames += 1
    # cvtColor rather than [:, :, ::-1]: the slice is a negative-stride view,
    # and cv2.resize copies it into a contiguous buffer on every call, which
    # cost 49 ms per frame on this part -- more than the detector itself
    out = pipe(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if out is not None:
        held += 1
        last = out
el = time.time() - t0
cap.release()

print("%d frames in %.1f s = %.1f fps" % (frames, el, frames / el))
print("hand held on %d of %d frames (%.0f%%), %d re-detects"
      % (held, frames, 100.0 * held / max(frames, 1), pipe.t["redetects"]))
t = pipe.t
n = max(t["frames"], 1)
for k in ("resize", "detect", "det_roi", "crop", "landmark", "post"):
    print("  %-9s %6.2f ms/frame" % (k, 1000.0 * t[k] / n))
if last is not None:
    world = np.asarray(last[0] if isinstance(last, tuple) else last)
    print("last output:", type(last).__name__,
          world.shape if hasattr(world, "shape") else "")
