"""Find out whether the worst frames are scattered or clustered, and around what.

The median disagreement is small enough to be ignorable but the upper tail is
not, and the two have completely different consequences. Scattered large errors
would mean the pipeline is unreliable frame to frame. Errors clustered just
after a re-detection would mean something narrower: the two sides start
following the hand from slightly different crops and take a few frames to
converge, which is a transient after an event that happens rarely.

Angle differences are wrapped first. Opposition is an atan2 result, so a pair
straddling the cut reads as a 337 degree disagreement when the hand moved 23.

    python3 tail_diag.py [refcap/frames.npz]
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/myohand/camera"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hand_mapping as hm
from hand_pipeline import HandPipeline

NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/myohand/nn/refcap/frames.npz")
d = np.load(NPZ)
frames, found, world, ref_img = d["frames"], d["found"], d["world"], d["img"]
H, W = frames.shape[1], frames.shape[2]
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")


def pts(a):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in a]


def wrap(x):
    x = abs(x) % 360.0
    return min(x, 360.0 - x)


pipe = HandPipeline()
rec = []
since = None          # frames elapsed since either side last re-detected
for i, bgr in enumerate(frames):
    out = pipe(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    mp_redet = bool(found[i] and (i == 0 or not found[i - 1]))
    if out is not None and (out["redetected"] or mp_redet):
        since = 0
    elif since is not None:
        since += 1
    if out is None or not found[i]:
        continue
    a, b = hm.raw_features(pts(out["world"])), hm.raw_features(pts(world[i]))
    rec.append((i, {k: wrap(a[k] - b[k]) for k in KEYS},
                float(np.abs(out["img"][:, :2] - ref_img[i][:, :2] * [W, H]).max()),
                since))

print("繞圈修正後的角度偏差（%d 幀）\n" % len(rec))
print("%-9s %8s %8s %8s" % ("角度", "中位", "p95", "最大"))
for k in KEYS:
    v = np.array([r[1][k] for r in rec])
    print("%-9s %7.2f° %7.2f° %7.2f°"
          % (k, np.median(v), np.percentile(v, 95), v.max()))

worst = np.array([max(r[1].values()) for r in rec])
idx = np.argsort(worst)[::-1][:8]
print("\n最差的 8 幀")
print("%6s %9s %9s %9s %9s %8s %9s"
      % ("幀", "curl_lo", "curl_hi", "thumb", "opp", "px", "重偵測後"))
for j in idx:
    i, dd, px, s = rec[j]
    print("%6d %8.1f° %8.1f° %8.1f° %8.1f° %7.1f %9s"
          % (i, dd["curl_lo"], dd["curl_hi"], dd["thumb"], dd["opp"], px,
             "%d 幀" % s if s is not None else "-"))

print("\n依「距離最近一次重偵測幾幀」分組")
print("%-12s %6s %9s %9s" % ("組別", "幀數", "最差中位", "最差 p95"))
for lo, hi, name in [(0, 0, "就是那一幀"), (1, 3, "之後 1-3 幀"),
                     (4, 10, "之後 4-10 幀"), (11, 10 ** 6, "之後 11 幀以上")]:
    sel = [w for w, r in zip(worst, rec)
           if r[3] is not None and lo <= r[3] <= hi]
    if sel:
        print("%-12s %6d %8.2f° %8.2f°"
              % (name, len(sel), np.median(sel), np.percentile(sel, 95)))
