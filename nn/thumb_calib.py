#!/usr/bin/env python3
"""Calibrate the thumb alone, keep the raw frames, and leave every other
key untouched.

camera/calibrate.py measures fingers and thumb in one pass and rewrites
the whole file, so a run aimed at the thumb also replaces CURL_OPEN and
CURL_CLOSED. This one replaces only the thumb keys and writes the rest
back verbatim.

Four differences from calibrate.py, each deliberate:

  * model_complexity is selectable and STILL DEFAULTS TO 1, which is
    wrong for this repo -- pass --complexity=0. The default came from a
    claim of mine that teleop_app.py runs the full model; it does not.
    Every entry point here is model_complexity=0 (teleop_app.py:256,
    calibrate.py:29, bench_vision.py:10, test_detect.py:11) and
    'git log -S model_complexity=1' is empty over all history.
    Measuring with one network and spending with another cost about 12
    degrees of median flexion here. The default is left as 1 only so
    that windows already derived with it stay reproducible.

  * every frame is kept in thumb_calib.csv, so a window can be re-derived
    later without asking the operator to perform the range again
    (--replay). The first run of this tool printed percentiles and threw
    the frames away, which meant a second recording just to see a
    histogram.

  * endpoints are reported raw and p2/p98, a histogram is printed, and
    nothing is written unless --save names one. A single hallucinated
    frame owning an endpoint is how THUMB_CLOSED reached 172.8 when the
    thumb only reaches about 71.

  * --only picks which channels to write. Opposition is derived from the
    palm-normal component, the least reliable thing MediaPipe reports,
    and its tails run past what a thumb can physically do; that channel
    deserves a look at the histogram before it is trusted.

Camera only: no EtherCAT, the hand cannot move. Writing is opt-in and
backs the old file up first.

    ../venv/bin/python3 thumb_calib.py [device] [seconds]
                                       [--save=raw|p98] [--only=a,b]
                                       [--complexity=0|1] [--replay]
"""
import csv
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

argv = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = dict((a.split("=", 1) + [""])[:2] for a in sys.argv[1:]
            if a.startswith("--"))
DEV = int(argv[0]) if argv else 0
SECS = float(argv[1]) if len(argv) > 1 else 20.0
SAVE = opts.get("--save", "")
CPLX = int(opts.get("--complexity", 1))
REPLAY = "--replay" in opts
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "thumb_calib.csv")

CHANNELS = ("flexion", "opposition", "abduction")
KEYS = {"flexion": ("THUMB_OPEN", "THUMB_CLOSED"),
        "opposition": ("OPP_MIN", "OPP_MAX"),
        "abduction": ("ABD_MIN", "ABD_MAX")}
ONLY = tuple(x.strip() for x in opts["--only"].split(",")) if opts.get("--only") \
    else CHANNELS
for c in ONLY:
    if c not in CHANNELS:
        sys.exit("--only takes any of: " + ", ".join(CHANNELS))
if SAVE and SAVE not in ("raw", "p98"):
    sys.exit("--save takes raw or p98")

LOCK = hm.HANDEDNESS
PLAUSIBLE = {"flexion": (0.0, 150.0),      # MCP + IP, 0 straight
             "opposition": (-20.0, 130.0),  # 0 in the palm plane, ~90 across
             "abduction": (0.0, 70.0)}      # splay from the index metacarpal


def pct(v, q):
    s = sorted(v)
    return round(s[min(len(s) - 1,
                       max(0, int(round(q / 100.0 * (len(s) - 1)))))], 1)


def histogram(name, values, width=48):
    lo, hi = min(values), max(values)
    n = 12
    step = (hi - lo) / n if hi > lo else 1.0
    bins = [0] * n
    for x in values:
        bins[min(n - 1, int((x - lo) / step))] += 1
    top = max(bins) or 1
    plo, phi = PLAUSIBLE[name]
    print("  %s, %d trusted frames" % (name, len(values)))
    for i, c in enumerate(bins):
        a, b = lo + i * step, lo + (i + 1) * step
        flag = "" if plo <= (a + b) / 2 <= phi else "  <- outside what a thumb can do"
        print("   %7.1f..%7.1f %5d %s%s"
              % (a, b, c, "#" * (c * width // top), flag))
    out = [x for x in values if not (plo <= x <= phi)]
    print("   outside %.0f..%.0f: %d of %d (%.1f%%)"
          % (plo, phi, len(out), len(values), 100.0 * len(out) / len(values)))


rows = []
if REPLAY:
    if not os.path.exists(CSV_PATH):
        sys.exit("no %s to replay" % CSV_PATH)
    for r in csv.DictReader(open(CSV_PATH)):
        r["trust"] = int(r["trust"])
        for k in CHANNELS:
            r[k] = float(r[k])
        rows.append(r)
    print("replaying %d frames from %s (camera not opened)"
          % (len(rows), CSV_PATH))
else:
    import cv2
    import mediapipe as mp

    hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=CPLX,
                                     min_detection_confidence=0.6,
                                     min_tracking_confidence=0.5)
    cap = cv2.VideoCapture(DEV)
    if not cap.isOpened():
        sys.exit("cannot open camera %d" % DEV)
    print("thumb-only calibration: /dev/video%d, %.0fs, model_complexity=%d (%s)"
          % (DEV, SECS, CPLX, "full" if CPLX else "lite"))
    print("handedness locked to %s; keep that hand palm-toward the camera\n"
          % LOCK)
    print(""" 0-5s   fist -> open, twice. keep the thumb outside the fist.
 5-10s  thumb splayed away from the index -> tucked against it, twice.
        stay flat in the palm plane; do not lift it toward the palm.
10-15s  thumb ROTATED toward the palm and back, twice.
        KEEP THE THUMB STRAIGHT -- rotation only, no bending, and do not
        reach for the pinky: touching it is rotation plus flexion, and
        the two arrive mixed together.
15-20s  thumb curled hard into the palm -> straight, twice.  <- THUMB_CLOSED
""")
    t0 = last = time.time()
    read = 0
    while time.time() - t0 < SECS:
        ok, frame = cap.read()
        if not ok:
            continue
        read += 1
        res = hands.process(cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB))
        if not res.multi_hand_world_landmarks:
            continue
        lbl = res.multi_handedness[0].classification[0]
        trust, reason = hm.thumb_trust(res.multi_hand_landmarks[0].landmark,
                                       lbl.label, lbl.score, LOCK)
        tf = hm.thumb_features(res.multi_hand_world_landmarks[0].landmark, LOCK)
        rows.append({"frame": read, "t": round(time.time() - t0, 3),
                     "label": lbl.label, "score": round(lbl.score, 3),
                     "trust": int(trust), "why": reason,
                     "flexion": round(tf["flexion"], 2),
                     "opposition": round(tf["opposition"], 2),
                     "abduction": round(tf["abduction"], 2)})
        if time.time() - last > 2:
            last = time.time()
            n = sum(r["trust"] for r in rows)
            print("  %4.0fs left   %s"
                  % (SECS - (last - t0),
                     "%d usable frames" % n if n else "no hand yet"), flush=True)
    cap.release()
    if not rows:
        sys.exit("no hand was seen")
    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\n%d frames -> %s" % (len(rows), CSV_PATH))

seen = {k: [r[k] for r in rows if r["trust"]] for k in CHANNELS}
if not seen["flexion"]:
    sys.exit("no frame passed the trust gate")
why = {}
for r in rows:
    if not r["trust"]:
        why[r["why"]] = why.get(r["why"], 0) + 1
print("tracked %d, trusted %d (%.0f%%)"
      % (len(rows), len(seen["flexion"]),
         100.0 * len(seen["flexion"]) / len(rows)))
if why:
    print("rejected: " + ", ".join("%s x%d" % kv for kv in sorted(why.items())))

print("\n=== distribution ===")
for k in CHANNELS:
    histogram(k, seen[k])
    print()

old = json.load(open(hm.CAL_PATH)) if os.path.exists(hm.CAL_PATH) else {}
prop = {"raw": {}, "p98": {}}
print("=== proposed ===   writing: %s" % ", ".join(ONLY))
print("%-14s %10s %12s %12s" % ("key", "current", "raw", "p98"))
for k in CHANNELS:
    lo_key, hi_key = KEYS[k]
    v = seen[k]
    prop["raw"][lo_key], prop["raw"][hi_key] = round(min(v), 1), round(max(v), 1)
    prop["p98"][lo_key], prop["p98"][hi_key] = pct(v, 2), pct(v, 98)
    mark = "" if k in ONLY else "   (skipped)"
    for key in (lo_key, hi_key):
        print("%-14s %10s %12s %12s%s"
              % (key, old.get(key, "-"), prop["raw"][key], prop["p98"][key], mark))

if not SAVE:
    print("\nnothing written. add --save=p98 (or --save=raw), and --only=... "
          "to pick channels.")
    sys.exit(0)

chosen = {}
for k in ONLY:
    for key in KEYS[k]:
        chosen[key] = prop[SAVE][key]
# --set overrides a percentile the histogram argued against. Opposition
# needed this: its p2 sat at -29.7 while the thumb rests near 8, and
# OPP_MIN is the value that must map to fully open.
for pair in filter(None, opts.get("--set", "").split(",")):
    key, _, val = pair.partition("=")
    if key not in {k for c in CHANNELS for k in KEYS[c]}:
        sys.exit("--set: unknown key %s" % key)
    chosen[key] = float(val)
    print("override: %s = %s" % (key, val))
backup = hm.CAL_PATH + ".bak-" + time.strftime("%m%d-%H%M")
shutil.copyfile(hm.CAL_PATH, backup)
merged = dict(old)
merged.update(chosen)
hm.save_calibration(merged)
print("\nbacked up -> %s" % backup)
print("wrote %s (%s endpoints):" % (hm.CAL_PATH, SAVE))
for k, v in merged.items():
    print("  %-14s %-8s%s" % (k, v, "  <-- changed" if old.get(k) != v else ""))
