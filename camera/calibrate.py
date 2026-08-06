#!/usr/bin/env python3
"""Watch the camera and report the range each mapping feature actually
covers, so the constants in hand_mapping come from a hand instead of a
guess. Run it, move through the full motion, read the suggested lines.

    python3 calibrate.py [device] [seconds]
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import mediapipe as mp

import hand_mapping as hm

dev = int(sys.argv[1]) if len(sys.argv) > 1 else 4
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

FEATURES = {
    "finger curl": lambda lm, h: min(hm.finger_curl(lm, c) for c in hm.FINGER_CHAINS.values()),
    "finger curl (max)": lambda lm, h: max(hm.finger_curl(lm, c) for c in hm.FINGER_CHAINS.values()),
    "thumb curl": lambda lm, h: hm.thumb_features(lm, h)["flexion"],
    "thumb opposition": lambda lm, h: hm.thumb_features(lm, h)["opposition"],
    "thumb splay": lambda lm, h: hm.thumb_features(lm, h)["abduction"],  # reported, not saved
}

cap = cv2.VideoCapture(dev)
hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                 min_detection_confidence=0.6)
seen = {k: [] for k in FEATURES}
votes = {}
print(f"move through the full range for {secs:.0f}s: fingers open to fist; "
      f"thumb splayed out, then swept across the palm to the pinky side")
t0 = last = time.time()
while time.time() - t0 < secs:
    ok, frame = cap.read()
    if not ok:
        continue
    res = hands.process(cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB))
    if res.multi_hand_world_landmarks:
        lm = res.multi_hand_world_landmarks[0].landmark
        lbl = res.multi_handedness[0].classification[0]
        votes[lbl.label] = votes.get(lbl.label, 0) + 1
        locked = max(votes, key=votes.get)   # running majority, settles fast
        trust, _ = hm.thumb_trust(res.multi_hand_landmarks[0].landmark,
                                  lbl.label, lbl.score, locked)
        for k, f in FEATURES.items():
            if k.startswith("thumb") and not trust:
                continue                     # never calibrate against guesses
            seen[k].append(f(lm, locked))
    if time.time() - last > 2:
        last = time.time()
        n = len(seen["thumb opposition"])
        print(f"  {secs - (last - t0):4.0f}s left   "
              f"{'tracking, keep moving' if n else 'no hand in frame yet'}", flush=True)
cap.release()

if not seen["thumb opposition"]:
    sys.exit("no hand was seen")
for k, v in seen.items():
    print(f"{k:20s} {min(v):6.1f} .. {max(v):6.1f}  ({len(v)} frames)")
c, t, o = seen["finger curl"], seen["thumb curl"], seen["thumb opposition"]
win = {"CURL_OPEN": round(min(c), 1),
       "CURL_CLOSED": round(max(seen["finger curl (max)"]), 1),
       "THUMB_OPEN": round(min(t), 1), "THUMB_CLOSED": round(max(t), 1),
       "OPP_MIN": round(min(o), 1), "OPP_MAX": round(max(o), 1),
       "HANDEDNESS": max(votes, key=votes.get)}
name = hm.save_calibration(win, note=f"calibrate.py, {secs:.0f}s of a real hand")
print(f"\nsaved to {hm.CAL_PATH} as profile '{name}':")
for k, v in win.items():
    print(f"  {k} = {v}")
