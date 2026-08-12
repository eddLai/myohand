"""Record frames together with what MediaPipe made of them.

Replacing the MediaPipe graph means reimplementing the parts it does not
expose: anchor decoding, non-maximum suppression, the rotated crop the
landmark model expects, and the tracking that lets most frames skip detection
entirely. Each of those carries constants that are easy to get slightly wrong
and hard to notice, and every calibration window in this project was measured
through the current pipeline, so landmarks that drift by a few degrees quietly
invalidate all of them.

The defence is to keep MediaPipe's own answer for the same frames. With both
saved, the replacement can be checked frame by frame against the thing it
replaces, offline, as many times as it takes.

Frames are stored raw rather than as video: an encoder would put its own
artefacts between the two pipelines and turn a mismatch into an argument about
compression.

  python3 ref_capture.py [device] [--frames=200] [--out=refcap]

Wave one hand around: near and far, palm and back, fast and slow, and a few
moments out of frame so the tracking has to recover.
"""

import os
import sys
import time

import cv2
import mediapipe as mp
import numpy as np

opts = {a.split("=")[0]: a.split("=", 1)[1]
        for a in sys.argv[1:] if a.startswith("--") and "=" in a}
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
DEV = int(pos[0]) if pos else 0
N = int(opts.get("--frames", 200))
OUT = opts.get("--out", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "refcap"))
CPLX = int(opts.get("--complexity", 0))
W, H = 1280, 720

os.makedirs(OUT, exist_ok=True)
hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=1,
                                 model_complexity=CPLX,
                                 min_detection_confidence=0.5,
                                 min_tracking_confidence=0.5)
draw = mp.solutions.drawing_utils

cap = cv2.VideoCapture(DEV)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
if not cap.isOpened():
    sys.exit("cannot open camera %d" % DEV)

print("錄 %d 幀到 %s" % (N, OUT))
print("請揮動一隻手：遠近、手心手背、快慢都要，中間讓手離開畫面兩三次。")
print("按 q 提早結束。\n")

frames, refs = [], []
hit = 0
while len(frames) < N:
    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    res = hands.process(rgb)

    rec = {"found": False}
    if res.multi_hand_world_landmarks:
        hit += 1
        lbl = res.multi_handedness[0].classification[0]
        rec = {"found": True, "label": lbl.label, "score": float(lbl.score),
               # image landmarks are what the ROI maths has to reproduce;
               # world landmarks are what every downstream angle is built on
               "img": np.array([[p.x, p.y, p.z] for p in
                                res.multi_hand_landmarks[0].landmark], np.float32),
               "world": np.array([[p.x, p.y, p.z] for p in
                                  res.multi_hand_world_landmarks[0].landmark],
                                 np.float32)}
        draw.draw_landmarks(frame, res.multi_hand_landmarks[0],
                            mp.solutions.hands.HAND_CONNECTIONS)
    frames.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    refs.append(rec)

    bar = int(300.0 * len(frames) / N)
    cv2.rectangle(frame, (20, H - 40), (320, H - 20), (60, 60, 60), -1)
    cv2.rectangle(frame, (20, H - 40), (20 + bar, H - 20), (90, 220, 120), -1)
    cv2.putText(frame, "%d/%d  hand in %d" % (len(frames), N, hit),
                (20, H - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1,
                cv2.LINE_AA)
    cv2.imshow("reference capture", frame)
    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
        break

cap.release()
cv2.destroyAllWindows()
for _ in range(10):
    cv2.waitKey(30)

if not frames:
    sys.exit("沒有錄到任何影格")

np.savez_compressed(
    os.path.join(OUT, "frames.npz"),
    frames=np.stack(frames),
    found=np.array([r["found"] for r in refs]),
    label=np.array([r.get("label", "") for r in refs]),
    score=np.array([r.get("score", 0.0) for r in refs], np.float32),
    img=np.stack([r["img"] if r["found"] else np.zeros((21, 3), np.float32)
                  for r in refs]),
    world=np.stack([r["world"] if r["found"] else np.zeros((21, 3), np.float32)
                    for r in refs]),
    complexity=np.array([CPLX]))

path = os.path.join(OUT, "frames.npz")
print("\n%d 幀，其中 %d 幀有手 -> %s (%.1f MB)"
      % (len(frames), hit, path, os.path.getsize(path) / 1e6))
if hit < len(frames) * 0.4:
    print("⚠️ 有手的幀偏少，重錄一次會比較好用")
if hit == len(frames):
    print("⚠️ 每一幀都有手，追蹤的恢復路徑沒被錄到；下次讓手離開畫面幾次")
