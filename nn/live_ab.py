#!/usr/bin/env python3
"""Both front ends on the same live frame, so nobody has to repeat a gesture.

The recording answered whether the two agree on 150 stored frames. What it
could not answer is whether they still agree on a live camera, where the
tracking state carries over between frames and each front end loses and
reacquires the hand on its own schedule. Feeding one frame to both removes
the only variable an operator cannot hold still: the gesture itself.

The window exists for the operator, not for the measurement: it shows both
skeletons so a hand drifting out of frame is obvious while there is still
time to fix it, and it calls the four gestures in turn so the disagreement
can be attributed to the gesture that produced it.

Everything is reported in counts, the units the driver speaks, against the
135 that made the quantised route unusable.

    python3 live_ab.py [--device N] [--headless]
"""
import os
import sys
import time
from collections import defaultdict

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/myohand-pipeline/camera"))
import mediapipe as mp

import hand_mapping as hm
import hand_pipeline as hp

AXES = ["pinky", "ring", "middle", "index", "thumb", "rot"]
ORANGE, PURPLE, INK, DIM = (60, 132, 245), (217, 84, 140), (30, 30, 30), (170, 170, 170)

# seconds, on-screen call, and why it is in the list
PHASES = [
    (3, "GET READY", "hold your hand up, palm to the camera"),
    (10, "OPEN AND CLOSE", "open, fist, open - keep repeating"),
    (10, "THUMB ACROSS PALM", "sweep the thumb over, then back out"),
    (14, "OUT OF FRAME AND BACK", "drop the hand away, bring it back - 4 times"),
    (10, "ROTATE", "palm, edge, back of hand, palm"),
]
DEVICE = int(sys.argv[sys.argv.index("--device") + 1]) if "--device" in sys.argv else 0
SHOW = "--headless" not in sys.argv

cap = cv2.VideoCapture(DEVICE)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
if not cap.isOpened():
    sys.exit(f"camera {DEVICE} did not open")

black = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                 min_detection_confidence=0.6,
                                 min_tracking_confidence=0.5)
mine = hp.MediaPipeHands(threads=4)
draw = mp.solutions.drawing_utils
CONN = mp.solutions.hands.HAND_CONNECTIONS


def read(res):
    """Targets, the trust gate and the drawable landmarks, or None if no hand."""
    if not res.multi_hand_world_landmarks:
        return None
    hd = res.multi_handedness[0].classification[0]
    world = res.multi_hand_world_landmarks[0].landmark
    img = res.multi_hand_landmarks[0]
    return (np.array(hm.pose_from_world_landmarks(world, hd.label), dtype=float),
            hm.thumb_trust(img.landmark, hd.label, hd.score, hd.label), img)


def spec(colour):
    return draw.DrawingSpec(color=colour, thickness=2, circle_radius=2)


stats = defaultdict(lambda: {"d": [], "trust": 0, "black": 0, "mine": 0, "none": 0})
total_s = sum(s for s, _, _ in PHASES)
t0 = time.time()
print(f"{total_s} s guided run on /dev/video{DEVICE}; follow the window", flush=True)

while True:
    elapsed = time.time() - t0
    if elapsed >= total_s:
        break
    acc, phase, hint = 0, PHASES[-1][1], PHASES[-1][2]
    for secs, name, why in PHASES:
        acc += secs
        if elapsed < acc:
            phase, hint, left = name, why, acc - elapsed
            break

    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    a, b = read(black.process(frame[:, :, ::-1])), read(mine.process(frame[:, :, ::-1]))
    s = stats[phase]
    if a and b:
        s["d"].append(np.abs(a[0] - b[0]))
        s["trust"] += a[1] != b[1]
    elif a:
        s["black"] += 1
    elif b:
        s["mine"] += 1
    else:
        s["none"] += 1

    if not SHOW:
        continue
    if a:
        draw.draw_landmarks(frame, a[2], CONN, spec(ORANGE), spec(ORANGE))
    if b:
        draw.draw_landmarks(frame, b[2], CONN, spec(PURPLE), spec(PURPLE))
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 96), INK, -1)
    cv2.rectangle(frame, (0, h - 34), (w, h), INK, -1)
    cv2.putText(frame, f"{phase}   {left:.0f}s", (18, 44),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (80, 200, 255), 2)
    cv2.putText(frame, hint, (18, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.6, DIM, 1)
    worst = f"{max(np.abs(a[0] - b[0])):.0f}" if a and b else "-"
    seen = ("both" if a and b else "mediapipe only" if a
            else "direct only" if b else "NO HAND IN FRAME")
    cv2.putText(frame, f"orange = mediapipe   purple = direct    {seen}"
                       f"    worst axis now: {worst} counts",
                (18, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (80, 200, 255) if a and b else (140, 140, 255), 1)
    cv2.imshow("A/B  mediapipe vs direct pipeline", frame)
    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
        break

cap.release()
cv2.destroyAllWindows()

alld = np.array([x for s in stats.values() for x in s["d"]], dtype=float)
n = len(alld)
print(f"\n{'phase':24s} {'both':>6s} {'mp only':>8s} {'dir only':>9s} "
      f"{'no hand':>8s} {'worst':>7s} {'trust':>6s}")
for _, name, _ in PHASES:
    s = stats[name]
    d = np.array(s["d"], dtype=float)
    worst = f"{d.max():.0f}" if len(d) else "-"
    print(f"{name:24s} {len(d):6d} {s['black']:8d} {s['mine']:9d} "
          f"{s['none']:8d} {worst:>7s} {s['trust']:6d}")

if not n:
    sys.exit("\nno frame had a hand on both sides; nothing to compare")

print(f"\n{n} comparable frames\n")
print(f"{'axis':8s} {'median':>8s} {'p90':>8s} {'worst':>8s} {'>135':>7s}")
for i, name in enumerate(AXES):
    print(f"{name:8s} {np.median(alld[:, i]):8.0f} "
          f"{np.percentile(alld[:, i], 90):8.0f} {alld[:, i].max():8.0f} "
          f"{100 * (alld[:, i] > 135).mean():6.0f}%")
splits = sum(s["trust"] for s in stats.values())
print(f"\nthumb trust gate disagreed on {splits} of {n} frames "
      f"({100 * splits / n:.0f}%)")
