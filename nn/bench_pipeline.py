"""Time the pipeline against MediaPipe's own, at several thread counts.

MediaPipe measured 245 ms per frame on this part, and the cause was not the
part: it pins XNNPACK to one thread on a 4-core CPU and its Python API offers no
way to change that. Running the two tflite files directly makes the thread count
settable, which is the entire reason for owning the code between them.

That code is Python and numpy where MediaPipe's was C++, so the models getting
faster is not by itself good news - the glue could give the gain straight back.
The stages are therefore timed separately, and the two paths are reported apart:
a frame that has to detect first costs far more than one that follows a hand
already found, and only the ratio between them says what the loop will feel like.

    python3 bench_pipeline.py bench40.npz [--threads=1,2,4] [--width=320]
"""

import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hand_pipeline import HandPipeline

opts = {a.split("=")[0]: a.split("=", 1)[1]
        for a in sys.argv[1:] if a.startswith("--") and "=" in a}
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
NPZ = pos[0] if pos else "refcap/bench40.npz"
THREADS = [int(x) for x in opts.get("--threads", "1,2,4").split(",")]
WIDTH = int(opts["--width"]) if "--width" in opts else None
PALM = opts.get("--palm")
LAND = opts.get("--landmark")

d = np.load(NPZ)
frames = d["frames"]
if WIDTH:
    h = int(round(frames.shape[1] * WIDTH / frames.shape[2]))
    frames = np.stack([cv2.resize(f, (WIDTH, h)) for f in frames])
rgb = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames]
print("%s  %d 幀  %dx%d  CPU %d 核"
      % (os.uname().nodename, len(rgb), rgb[0].shape[1], rgb[0].shape[0],
         os.cpu_count()))

STAGES = [("resize", "縮圖"), ("detect", "palm+解框+NMS"), ("det_roi", "算 ROI"),
          ("crop", "旋轉裁切"), ("landmark", "landmark 模型"), ("post", "反算座標")]

for n in THREADS:
    pipe = HandPipeline(palm=PALM, landmark=LAND, threads=n)
    for f in rgb[:3]:
        pipe(f)                                   # warm up
    pipe.reset()
    pipe.reset_timing()
    t0 = time.perf_counter()
    for f in rgb:
        pipe(f)
    wall = time.perf_counter() - t0
    t = pipe.t
    nf, nd = t["frames"], t["redetects"]
    print("\n%d 執行緒   %d 幀 %.2f 秒 = %.1f FPS 平均   （重偵測 %d 幀）"
          % (n, nf, wall, nf / wall, nd))
    model = t["detect"] + t["landmark"]
    glue = t["resize"] + t["det_roi"] + t["crop"] + t["post"]
    for k, name in STAGES:
        per = t[k] / (nd if k in ("resize", "detect", "det_roi") else nf)
        print("   %-14s %7.1f ms/次   共 %6.1f ms" % (name, per * 1e3,
                                                     t[k] * 1e3))
    print("   %-14s %7.1f ms   膠水 %.1f ms（%.0f%%）"
          % ("模型合計", model * 1e3, glue * 1e3,
             100.0 * glue / (model + glue) if model + glue else 0))
    if nf > nd:
        tracked = (t["crop"] + t["landmark"] + t["post"]) / nf
        print("   追蹤中的一幀 %.1f ms → %.1f FPS" % (tracked * 1e3, 1 / tracked))

# the thing being replaced, on the same frames
try:
    import mediapipe as mp
    hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=1,
                                     model_complexity=0,
                                     min_detection_confidence=0.5,
                                     min_tracking_confidence=0.5)
    for f in rgb[:3]:
        hands.process(f)
    t0 = time.perf_counter()
    for f in rgb:
        hands.process(f)
    w = time.perf_counter() - t0
    print("\nMediaPipe 黑盒（同樣影像）  %.2f 秒 = %.1f FPS   每幀 %.1f ms"
          % (w, len(rgb) / w, w / len(rgb) * 1e3))
except Exception as e:
    print("\nMediaPipe 對照跑不起來：%s" % e)
