#!/usr/bin/env python3
"""How long does the disagreement last after the hand comes back?

live_ab.py located the outliers rather than explaining them: every count over
the deadband sits in the phase where the hand leaves the frame and returns,
and thumb opposition peaks at most of its travel there. Size alone does not
say whether the hand would twitch or lurch, because a spike lasting two
frames is filtered by the driver's own rate limit and one lasting twenty is
not.

So each reacquisition is timed rather than aggregated. Both front ends are
also compared against themselves a few frames later, since a disagreement
says only that they differ, not which of them is still converging.

    python3 redetect_ab.py [--device N] [--seconds S]
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/myohand-pipeline/camera"))
import mediapipe as mp

import hand_mapping as hm
import hand_pipeline as hp

DEADBAND = 12          # hand_sink.TeleopSink: below this nothing is sent
SETTLED = 5            # frames of agreement before an event is called over
DEVICE = int(sys.argv[sys.argv.index("--device") + 1]) if "--device" in sys.argv else 0
SECONDS = float(sys.argv[sys.argv.index("--seconds") + 1]) if "--seconds" in sys.argv else 40.0

cap = cv2.VideoCapture(DEVICE)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
if not cap.isOpened():
    sys.exit(f"camera {DEVICE} did not open")

black = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                 min_detection_confidence=0.6,
                                 min_tracking_confidence=0.5)
mine = hp.MediaPipeHands(threads=4)
draw, CONN = mp.solutions.drawing_utils, mp.solutions.hands.HAND_CONNECTIONS
ORANGE, PURPLE, INK = (60, 132, 245), (217, 84, 140), (30, 30, 30)


def read(res):
    if not res.multi_hand_world_landmarks:
        return None
    hd = res.multi_handedness[0].classification[0]
    return (np.array(hm.pose_from_world_landmarks(
        res.multi_hand_world_landmarks[0].landmark, hd.label), dtype=float),
        res.multi_hand_landmarks[0])


def spec(c):
    return draw.DrawingSpec(color=c, thickness=2, circle_radius=2)


log = []               # (had_hand, diff vector, a targets, b targets)
t0 = time.time()
print(f"{SECONDS:.0f} s on /dev/video{DEVICE}: hold the hand up, drop it out of "
      f"frame, bring it back. Repeat as often as you can.", flush=True)

while time.time() - t0 < SECONDS:
    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    a, b = read(black.process(frame[:, :, ::-1])), read(mine.process(frame[:, :, ::-1]))
    both = a is not None and b is not None
    log.append((both, np.abs(a[0] - b[0]) if both else None,
                a[0] if a else None, b[0] if b else None))

    if a:
        draw.draw_landmarks(frame, a[1], CONN, spec(ORANGE), spec(ORANGE))
    if b:
        draw.draw_landmarks(frame, b[1], CONN, spec(PURPLE), spec(PURPLE))
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 60), INK, -1)
    left = SECONDS - (time.time() - t0)
    events = sum(1 for i in range(1, len(log)) if log[i][0] and not log[i - 1][0])
    cv2.putText(frame, f"DROP IT AND BRING IT BACK   {left:.0f}s   "
                       f"{events} reacquisitions", (18, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 200, 255), 2)
    cv2.imshow("reacquisition timing", frame)
    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
        break

cap.release()
cv2.destroyAllWindows()

events = [i for i in range(1, len(log)) if log[i][0] and not log[i - 1][0]]
print(f"\n{len(log)} frames, {len(events)} reacquisitions\n")
if not events:
    sys.exit("no reacquisition captured; the hand never left the frame")

print("frames from the hand reappearing until the two agree again:\n")
print(f"{'event':>5s} {'first':>7s} {'>135 for':>9s} {'>12 for':>8s} "
      f"{'mp settles':>11s} {'mine settles':>13s}")
rows = []
for e in events:
    seq = []
    for i in range(e, len(log)):
        if not log[i][0]:
            break
        seq.append(i)
    if len(seq) < SETTLED + 1:
        continue
    d = np.array([log[i][1].max() for i in seq])
    over135 = int(np.argmax(d <= 135)) if (d <= 135).any() else len(d)
    over12 = int(np.argmax(d <= DEADBAND)) if (d <= DEADBAND).any() else len(d)
    # each side against its own value once things have quietened down
    ref = min(len(seq) - 1, 15)
    mp_move = np.abs(log[seq[0]][2] - log[seq[ref]][2]).max()
    my_move = np.abs(log[seq[0]][3] - log[seq[ref]][3]).max()
    rows.append((d[0], over135, over12, mp_move, my_move))
    print(f"{len(rows):5d} {d[0]:7.0f} {over135:9d} {over12:8d} "
          f"{mp_move:11.0f} {my_move:13.0f}")

if not rows:
    sys.exit("\nreacquisitions were too short to time; try holding the hand "
             "up a little longer each time")
r = np.array(rows, dtype=float)
print(f"\n{'':5s} {'first':>7s} {'>135 for':>9s} {'>12 for':>8s} "
      f"{'mp settles':>11s} {'mine settles':>13s}")
print(f"{'med':>5s} {np.median(r[:, 0]):7.0f} {np.median(r[:, 1]):9.0f} "
      f"{np.median(r[:, 2]):8.0f} {np.median(r[:, 3]):11.0f} "
      f"{np.median(r[:, 4]):13.0f}")
print(f"{'worst':>5s} {r[:, 0].max():7.0f} {r[:, 1].max():9.0f} "
      f"{r[:, 2].max():8.0f} {r[:, 3].max():11.0f} {r[:, 4].max():13.0f}")
print(f"\nfirst   = worst axis on the frame the hand reappears, in counts\n"
      f">135 for = frames above the count that made the DPU route unusable\n"
      f">12 for  = frames above the sink deadband, ie frames the hand would see\n"
      f"settles  = how far each side moves from its own first frame to frame 15;\n"
      f"           a large number there means that side was still converging")
