#!/usr/bin/env python3
"""Measure the vision half of the teleop loop on this host."""
import statistics, sys, time
import cv2
import mediapipe as mp

cap = cv2.VideoCapture(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                 min_detection_confidence=0.6,
                                 min_tracking_confidence=0.5)
grab, infer = [], []
hits = 0
for i in range(120):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t1 = time.perf_counter()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    t2 = time.perf_counter()
    if i > 10:                      # skip warm-up
        grab.append((t1 - t0) * 1000)
        infer.append((t2 - t1) * 1000)
    if res.multi_hand_landmarks:
        hits += 1

def rep(name, v):
    print(f"{name:22s} mean {statistics.mean(v):6.1f} ms   "
          f"p50 {statistics.median(v):6.1f}   max {max(v):6.1f}")

rep("camera grab", grab)
rep("mediapipe inference", infer)
print(f"total per frame        {statistics.mean(grab) + statistics.mean(infer):6.1f} ms "
      f"-> {1000 / (statistics.mean(grab) + statistics.mean(infer)):.1f} FPS ceiling")
print(f"frames with a hand     {hits}/120")
cap.release()
