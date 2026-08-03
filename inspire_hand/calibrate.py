#!/usr/bin/env python3
"""Watch the camera and report the range each mapping feature actually
covers, so the constants in hand_mapping come from a hand instead of a
guess. Run it, move through the full motion, read the suggested lines.

    python3 calibrate.py [device] [seconds]
"""
import sys, time
import cv2
import mediapipe as mp

import hand_mapping as hm

dev = int(sys.argv[1]) if len(sys.argv) > 1 else 4
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

FEATURES = {
    "finger curl": lambda lm: min(hm.finger_curl(lm, c) for c in hm.FINGER_CHAINS.values()),
    "finger curl (max)": lambda lm: max(hm.finger_curl(lm, c) for c in hm.FINGER_CHAINS.values()),
    "thumb curl": lambda lm: hm._joint_angle(lm, 1, 2, 3) + hm._joint_angle(lm, 2, 3, 4),
    "thumb abduction": hm.thumb_abduction,
}

cap = cv2.VideoCapture(dev)
hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                 min_detection_confidence=0.6)
seen = {k: [] for k in FEATURES}
print(f"move through the full range for {secs:.0f}s: fingers open to fist, "
      f"thumb tucked to splayed")
t0 = time.time()
while time.time() - t0 < secs:
    ok, frame = cap.read()
    if not ok:
        continue
    res = hands.process(cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB))
    if res.multi_hand_world_landmarks:
        lm = res.multi_hand_world_landmarks[0].landmark
        for k, f in FEATURES.items():
            seen[k].append(f(lm))
cap.release()

if not seen["thumb abduction"]:
    sys.exit("no hand was seen")
for k, v in seen.items():
    print(f"{k:20s} {min(v):6.1f} .. {max(v):6.1f}  ({len(v)} frames)")
c, t, a = seen["finger curl"], seen["thumb curl"], seen["thumb abduction"]
print("\nsuggested constants for hand_mapping.py:")
print(f"CURL_OPEN, CURL_CLOSED = {min(c):.1f}, {max(seen['finger curl (max)']):.1f}")
print(f"THUMB_OPEN, THUMB_CLOSED = {min(t):.1f}, {max(t):.1f}")
print(f"ABD_MIN, ABD_MAX = {min(a):.1f}, {max(a):.1f}")
