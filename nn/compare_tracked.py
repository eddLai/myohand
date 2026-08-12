"""Score the tracking pipeline against MediaPipe, in degrees and in motor counts.

The recording was made frame by frame through MediaPipe's own tracking, so the
replacement has to be run the same way - in order, carrying its state - for the
two to be comparing like with like. Frames are reported split by whether each
side had just re-detected, because a disagreement there points at the detection
geometry while one on a followed frame points at the tracking rule.

The number that decides anything is the last one: how many motor counts the
disagreement is worth, against the 816 counts of thumb travel the calibration
windows span.

    python3 compare_tracked.py [refcap/frames.npz]
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

pipe = HandPipeline()
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")


def pts(arr):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in arr]


rows, mine_only, mp_only, flags = [], 0, 0, []
for i, bgr in enumerate(frames):
    out = pipe(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if out is None:
        mp_only += int(found[i])
        continue
    flags.append(out["flag"])
    if not found[i]:
        mine_only += 1
        continue
    a, b = hm.raw_features(pts(out["world"])), hm.raw_features(pts(world[i]))
    px = np.abs(out["img"][:, :2] - ref_img[i][:, :2] * [W, H]).max()
    rows.append(({k: abs(a[k] - b[k]) for k in KEYS}, px,
                 out["redetected"], bool(i == 0 or not found[i - 1])))

print("%d 幀，MediaPipe 找到 %d" % (len(frames), found.sum()))
print("我找到但 MediaPipe 沒有 %d，MediaPipe 找到但我沒有 %d，可比對 %d"
      % (mine_only, mp_only, len(rows)))
print("presence 分數 中位 %.3f 最小 %.3f（門檻 %.2f）\n"
      % (np.median(flags), min(flags), pipe.presence))
if not rows:
    sys.exit("沒有可比對的幀")


def show(name, sel):
    if not sel:
        print("\n%s：無" % name)
        return
    px = np.array([r[1] for r in sel])
    print("\n%s（%d 幀）   點位偏差 中位 %.1f px  p95 %.1f px"
          % (name, len(sel), np.median(px), np.percentile(px, 95)))
    for k in KEYS:
        v = np.array([r[0][k] for r in sel])
        print("   %-9s 中位 %6.2f°  p95 %6.2f°  最大 %6.2f°"
              % (k, np.median(v), np.percentile(v, 95), v.max()))


show("全部", rows)
show("兩邊都剛重新偵測", [r for r in rows if r[2] and r[3]])
show("兩邊都在追蹤", [r for r in rows if not r[2] and not r[3]])

cpd = (1850 - 1034) / (hm.THUMB_CLOSED - hm.THUMB_OPEN)
worst = max(np.median(np.array([r[0][k] for r in rows])) for k in KEYS)
print("\n拇指彎曲 1° = %.1f counts（校正窗涵蓋 816 counts）" % cpd)
print("最差的中位偏差 %.2f° = %.0f counts" % (worst, worst * cpd))
print("對照：同一份影像跨機器重跑 MediaPipe，對掌中位 0.09°、最大 5.92°")
