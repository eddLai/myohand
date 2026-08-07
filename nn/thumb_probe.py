#!/usr/bin/env python3
"""Per-frame dump of the thumb channels and the trust gate, run through
both MediaPipe landmark models on the same frames.

Two questions get answered in one recording:

  1. Does the landmark model change the channels?  calibrate.py opens
     Hands with model_complexity=0 (hand_landmark_lite) while
     teleop_app.py opens it with 1 (hand_landmark_full), so the window is
     measured with one network and spent by another.

  2. Is the window the operator's range, or the network's?  The summary
     prints the window raw min/max and p2/p98 for both models.

Read the summary in this order -- the first run of this probe drew a
conclusion from section B before it existed, and the conclusion was
wrong:

  A  coverage      how much of the recording carried a hand at all
  B  paired        both models on the SAME frames -- the only honest
                   model-vs-model comparison
  C  per model     each model's own distribution; comparable only
                   because B's frame set is used, not each model's
                   trusted subset (those cover different seconds of the
                   recording, and the hand was doing different things)
  D  targets       what the current window turns the channels into
  E  window        what a calibration run would have written

Handedness is locked to calibration.json rather than to a running
majority of the labels. calibrate.py locks by running majority, which
lets the first few frames decide -- and the labels flip often enough
that an early wrong lock rejects the rest of the recording. The probe
reports both so the cost of that choice is visible.

Camera only. This never opens the EtherCAT bus and cannot move the hand.
It does not write calibration.json; calibrate.py still owns it.

    ../venv/bin/python3 thumb_probe.py [device] [seconds]
"""
import csv
import os
import sys
import time

import cv2
import mediapipe as mp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

DEV = int(sys.argv[1]) if len(sys.argv) > 1 else 0
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "thumb_probe.csv")

COMPLEXITIES = (0, 1)
NAME = {0: "lite", 1: "full"}
CHANNELS = ("flexion", "abduction", "opposition")

LOCK = hm.HANDEDNESS                      # from calibration.json
OPP_LO = getattr(hm, "OPP_MIN", getattr(hm, "ABD_MIN", 10.0))
OPP_HI = getattr(hm, "OPP_MAX", getattr(hm, "ABD_MAX", 90.0))


def pct(v, q):
    s = sorted(v)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def line(tag, v):
    print("    %-11s min %7.1f  p2 %7.1f  p50 %7.1f  p98 %7.1f  max %7.1f"
          % (tag, min(v), pct(v, 2), pct(v, 50), pct(v, 98), max(v)))


def targets(flex, opp):
    return (hm._scale(flex, hm.THUMB_CLOSED, hm.THUMB_OPEN, hm.T_MIN, hm.T_MAX),
            hm._scale(opp, OPP_HI, OPP_LO, hm.ROT_MIN, hm.T_MAX))


nets = {c: mp.solutions.hands.Hands(max_num_hands=1, model_complexity=c,
                                    min_detection_confidence=0.6,
                                    min_tracking_confidence=0.5)
        for c in COMPLEXITIES}
rows = []
per = {c: {} for c in COMPLEXITIES}        # frame -> record
votes = {c: {} for c in COMPLEXITIES}      # for the majority-lock comparison

cap = cv2.VideoCapture(DEV)
if not cap.isOpened():
    sys.exit("cannot open camera %d (try 0)" % DEV)

print("reading /dev/video%d for %.0fs through both models." % (DEV, SECS))
print("keep the hand in frame the whole time and move slowly through the "
      "full range:\n  fingers open -> fist -> open, thumb splayed out -> "
      "swept across the palm to the pinky side")
print("handedness locked to %s (from calibration.json)" % LOCK)
print("current window: THUMB_OPEN %s .. THUMB_CLOSED %s, opposition %s .. %s"
      % (hm.THUMB_OPEN, hm.THUMB_CLOSED, OPP_LO, OPP_HI))

read = 0
t0 = last = time.time()
while time.time() - t0 < SECS:
    ok, frame = cap.read()
    if not ok:
        continue
    read += 1
    rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
    stamp = time.time() - t0
    for c in COMPLEXITIES:
        res = nets[c].process(rgb)
        if not res.multi_hand_world_landmarks:
            continue
        world = res.multi_hand_world_landmarks[0].landmark
        image = res.multi_hand_landmarks[0].landmark
        lbl = res.multi_handedness[0].classification[0]
        votes[c][lbl.label] = votes[c].get(lbl.label, 0) + 1
        major = max(votes[c], key=votes[c].get)
        trust, why = hm.thumb_trust(image, lbl.label, lbl.score, LOCK)
        t_maj, _ = hm.thumb_trust(image, lbl.label, lbl.score, major)
        tf = hm.thumb_features(world, LOCK)
        bend, rot = targets(tf["flexion"], tf["opposition"])
        rec = {"frame": read, "t": round(stamp, 3), "model": NAME[c],
               "complexity": c, "label": lbl.label, "score": round(lbl.score, 3),
               "trust": int(trust), "why": why, "trust_majority": int(t_maj),
               "flexion": round(tf["flexion"], 2),
               "abduction": round(tf["abduction"], 2),
               "opposition": round(tf["opposition"], 2),
               "tgt_bend": bend, "tgt_rot": rot}
        rows.append(rec)
        per[c][read] = rec
    if time.time() - last > 2:
        last = time.time()
        n = len(per[1])
        print("  %4.0fs left   %s"
              % (SECS - (last - t0),
                 "tracking, keep moving" if n else "no hand in frame yet"),
              flush=True)
cap.release()

if not rows:
    sys.exit("no hand was seen")
with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print("\n%d rows -> %s" % (len(rows), OUT))

# --- A. coverage -----------------------------------------------------
print("\n=== A. coverage ===")
print("  frames read %d over %.1fs (%.1f fps)" % (read, SECS, read / SECS))
for c in COMPLEXITIES:
    tr = [r for r in per[c].values() if r["trust"]]
    maj = [r for r in per[c].values() if r["trust_majority"]]
    why = {}
    for r in per[c].values():
        if not r["trust"]:
            why[r["why"]] = why.get(r["why"], 0) + 1
    print("  %-4s hand on %3d frames (%4.1f%%), trusted %3d (%4.1f%% of read)"
          % (NAME[c], len(per[c]), 100.0 * len(per[c]) / read,
             len(tr), 100.0 * len(tr) / read))
    print("       majority-lock would trust %d -- the lock choice is worth %+d frames"
          % (len(maj), len(tr) - len(maj)))
    if why:
        print("       rejected: " + ", ".join("%s x%d" % kv
                                              for kv in sorted(why.items())))

paired = sorted(set(per[0]) & set(per[1]))
both = [f for f in paired if per[0][f]["trust"] and per[1][f]["trust"]]
print("  frames both models saw: %d, both trusted: %d" % (len(paired), len(both)))
if not both:
    sys.exit("\nno frame was trusted by both models -- nothing comparable")

# --- B. paired -------------------------------------------------------
print("\n=== B. paired: both models, same %d frames ===" % len(both))
for k in CHANNELS:
    d = sorted(abs(per[1][f][k] - per[0][f][k]) for f in both)
    print("    %-11s |full-lite|  p50 %6.1f  p90 %6.1f  max %6.1f"
          % (k, d[len(d) // 2], d[int(len(d) * 0.9)], d[-1]))
gaps = [per[1][f]["opposition"] - per[0][f]["opposition"] for f in both]
wrapish = sum(1 for g in gaps if min(abs(abs(g) - 360), abs(g)) < 20)
print("    opposition gaps within 20 deg of 0 or +-360: %d/%d "
      "(a wrap would put nearly all of them here)" % (wrapish, len(gaps)))

# --- C. per model, on the paired frames ------------------------------
print("\n=== C. per model, restricted to those same %d frames ===" % len(both))
for c in COMPLEXITIES:
    print("  --- %s ---" % NAME[c])
    for k in CHANNELS:
        line(k, [per[c][f][k] for f in both])

# --- D. targets ------------------------------------------------------
print("\n=== D. what the current window makes of them ===")
for c in COMPLEXITIES:
    b = [per[c][f]["tgt_bend"] for f in both]
    o = [per[c][f]["tgt_rot"] for f in both]
    print("  %-4s tgt_bend %4d..%4d  saturated %5.1f%%   "
          "tgt_rot %4d..%4d  saturated %5.1f%%"
          % (NAME[c], min(b), max(b),
             100.0 * sum(1 for x in b if x in (hm.T_MIN, hm.T_MAX)) / len(b),
             min(o), max(o),
             100.0 * sum(1 for x in o if x in (hm.ROT_MIN, hm.T_MAX)) / len(o)))
print("  full travel is %d..%d; a bend floor well above %d means the thumb "
      "cannot close" % (hm.T_MIN, hm.T_MAX, hm.T_MIN))

# --- E. window -------------------------------------------------------
print("\n=== E. window a calibration run would have written ===")
print("  %-18s %11s %13s %9s %9s"
      % ("", "THUMB_OPEN", "THUMB_CLOSED", "OPP_MIN", "OPP_MAX"))
for c in COMPLEXITIES:
    tr = [f for f in per[c] if per[c][f]["trust"]]
    if not tr:
        continue
    t = [per[c][f]["flexion"] for f in tr]
    o = [per[c][f]["opposition"] for f in tr]
    print("  %-4s raw min/max   %11.1f %13.1f %9.1f %9.1f"
          % (NAME[c], min(t), max(t), min(o), max(o)))
    print("  %-4s p2/p98        %11.1f %13.1f %9.1f %9.1f"
          % (NAME[c], pct(t, 2), pct(t, 98), pct(o, 2), pct(o, 98)))
print("\nin use now: THUMB_OPEN %s  THUMB_CLOSED %s  OPP %s..%s"
      % (hm.THUMB_OPEN, hm.THUMB_CLOSED, OPP_LO, OPP_HI))
print("nothing was saved. calibrate.py still owns calibration.json.")
