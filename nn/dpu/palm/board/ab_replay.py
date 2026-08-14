"""Record once, replay through both palm front ends.

The live loop loses the hand on most frames and there are two candidates:
the DPU palm detector, or everything downstream of it. Asking a person to
hold a pose twice does not separate them, because the two runs would see
different hands. One recording played through both settings does.

Frames are held as JPEG in memory rather than raw: 150 raw 720p frames are
close to half a gigabyte on a board with 1.9 GB and no swap.

    python ab_replay.py [n_frames]
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/pipe_bench"))
sys.path.append(os.path.expanduser(
    "~/rh56f1_kd240/env/lib/python3.10/site-packages"))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
MODELS = os.path.expanduser("~/rh56f1_kd240/models")
PALM = os.path.join(MODELS, "palm_detection_lite.tflite")
LAND = os.path.join(MODELS, "hand_landmark_lite.tflite")

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_FPS, 30)
for _ in range(5):
    cap.read()

print("recording %d frames -- keep the hand in view" % N)
buf = []
t0 = time.time()
while len(buf) < N:
    ok, bgr = cap.read()
    if not ok:
        break
    buf.append(cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])[1])
cap.release()
print("recorded %d frames in %.1f s (%.1f fps)"
      % (len(buf), time.time() - t0, len(buf) / (time.time() - t0)))

frames = [cv2.cvtColor(cv2.imdecode(b, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
          for b in buf]
del buf

import hand_pipeline as hp


def run(tag, use_dpu, thresh=None):
    pipe = hp.HandPipeline(palm=PALM, landmark=LAND, threads=4)
    if use_dpu:
        from dpu_palm import DpuPalm
        run.dpu = getattr(run, "dpu", None) or DpuPalm()
        run.dpu.attach(pipe.det)
    if thresh is not None:
        pipe.det.min_score_thresh = thresh
    held, first = 0, None
    t0 = time.time()
    for i, f in enumerate(frames):
        out = pipe(f)
        if out is not None:
            held += 1
            if first is None:
                first = i
    el = time.time() - t0
    print("%-22s thresh %.2f  held %3d/%d (%3.0f%%)  redetects %3d  "
          "first hand at frame %s  %.1f fps"
          % (tag, pipe.det.min_score_thresh, held, len(frames),
             100.0 * held / len(frames), pipe.t["redetects"],
             first if first is not None else "-", len(frames) / el))
    return pipe


print()
run("tflite palm", False)
run("DPU palm", True, 0.35)
run("DPU palm, looser", True, 0.20)
