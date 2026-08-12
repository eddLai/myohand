"""Report whether a reference capture covers the cases the rewrite can fail on.

A replacement pipeline that matches MediaPipe on easy frames proves nothing: the
crop geometry only misbehaves at the edges of the range, and the detection path
only runs on the frames where tracking has just been lost. A recording that
holds the hand still in the middle of the frame would pass a comparison and
still hide both.

    python3 refcap_audit.py [refcap/frames.npz]
"""

import os
import sys

import numpy as np

PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "refcap", "frames.npz")

d = np.load(PATH)
img, found = d["img"], d["found"]
n, hit = len(found), int(found.sum())
print("%s  %.0f MB" % (PATH, os.path.getsize(PATH) / 1e6))
print("%d 幀，%d 幀有手 (%.0f%%)，complexity=%d，畫面 %dx%d"
      % (n, hit, 100.0 * hit / n, d["complexity"][0],
         d["frames"].shape[2], d["frames"].shape[1]))
if hit < 20:
    sys.exit("有手的幀太少，無法比對")

f = np.where(found)[0]
L = img[f]                                   # (k, 21, 3) normalised image coords

# how big the hand is on screen: the crop scale the ROI maths has to reproduce
span = np.maximum(L[:, :, 0].ptp(1), L[:, :, 1].ptp(1))
# which way the palm faces: the wrist triangle flips sign between palm and back
v1, v2 = L[:, 5, :2] - L[:, 0, :2], L[:, 17, :2] - L[:, 0, :2]
face = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
# how fast it moves between consecutive kept frames
step = np.full(len(f), np.nan)
adj = np.diff(f) == 1
step[1:][adj] = np.linalg.norm(L[1:, 0, :2] - L[:-1, 0, :2], axis=1)[adj]
# in-plane rotation, the part of the crop most easily got wrong
ang = np.degrees(np.arctan2(*(L[:, 9, :2] - L[:, 0, :2]).T[::-1]))


def band(name, v, edges, unit=""):
    v = v[np.isfinite(v)]
    cells = []
    for lo, hi in zip(edges, edges[1:]):
        c = int(((v >= lo) & (v < hi)).sum())
        cells.append("%s%-6s %4d %s" % ("  " if c else "⚠ ", "%g-%g" % (lo, hi),
                                        c, unit))
    print("\n%s（%.3f 到 %.3f）" % (name, v.min(), v.max()))
    for c in cells:
        print("   " + c)
    return sum(1 for c in cells if c.startswith("⚠"))


gaps = []
run = 0
for ok in found:
    if ok:
        if run:
            gaps.append(run)
        run = 0
    else:
        run += 1
if run:
    gaps.append(run)
redetect = int(sum(1 for i in f if i and not found[i - 1]))

holes = 0
holes += band("手在畫面上的大小 near/far", span, [0.0, 0.2, 0.35, 0.5, 0.7, 1.0])
holes += band("手心 / 手背 palm/back", face, [-0.4, -0.05, 0.0, 0.05, 0.4])
holes += band("每幀移動量 slow/fast", step, [0.0, 0.01, 0.03, 0.08, 1.0])
holes += band("手的傾角 in-plane rotation", ang, [-180, -90, -20, 20, 90, 180])

print("\n追丟與恢復 tracking")
print("   斷開 %d 次，長度 %s" % (len(gaps), gaps[:12] or "無"))
print("   %s重新偵測的幀 %d（palm_detection 真正跑到的只有這些）"
      % ("  " if redetect >= 3 else "⚠ ", redetect))
if redetect < 3:
    holes += 1

print("\n" + ("這份夠用。" if not holes else
              "⚠ %d 個區間是空的 —— 空的地方比對不到，換上去之後那些情況沒被驗過。"
              % holes))
