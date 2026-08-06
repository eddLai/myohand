#!/usr/bin/env python3
"""Verify webcam + MediaPipe hand detection; print per-finger flexion."""
import cv2, sys

CAM = int(sys.argv[1]) if len(sys.argv) > 1 else 4

import mediapipe as mp
try:
    mp_hands = mp.solutions.hands
    hands = mp_hands.Hands(static_image_mode=False, max_num_hands=1,
                           model_complexity=0, min_detection_confidence=0.5,
                           min_tracking_confidence=0.5)
    API = "solutions"
except Exception as e:
    print("legacy solutions API unavailable:", e)
    sys.exit(3)

cap = cv2.VideoCapture(CAM)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
if not cap.isOpened():
    print("camera open failed"); sys.exit(2)

def dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5

FINGERS = {"index": (5, 8), "middle": (9, 12), "ring": (13, 16), "pinky": (17, 20)}

found = 0
for f in range(120):
    ok, frame = cap.read()
    if not ok:
        continue
    res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if res.multi_hand_landmarks:
        lm = res.multi_hand_landmarks[0].landmark
        w = lm[0]
        vals = {}
        for name, (mcp, tip) in FINGERS.items():
            vals[name] = dist(lm[tip], w) / max(dist(lm[mcp], w), 1e-6)
        vals["thumb"] = dist(lm[4], w) / max(dist(lm[2], w), 1e-6)
        found += 1
        if found % 5 == 1:
            print("hand!", {k: round(v, 2) for k, v in vals.items()})
        cv2.imwrite("/tmp/detect_view.jpg", frame)
    if found >= 15:
        break
print(f"frames with hand: {found}")
cap.release()
