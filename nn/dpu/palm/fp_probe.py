"""What a lower threshold costs in false detections.

Recall is free to buy if nobody checks what it was bought with. 33 of the 150
recorded frames are ones the float model declines to call a hand, and those
are the only honest negatives available, so they decide whether dropping the
threshold recovers real detections or merely invents them.

A false positive here is worse than a miss: a miss holds the previous crop,
while a false positive cuts the next 224x224 window around nothing and the
landmark model then reports a hand pose for it.

    python fp_probe.py <tag> [tag ...]
"""
import sys

import numpy as np

sys.path.insert(0, ".")
from blazedetector import BlazeDetector

SHAPES = [(1, 108, 12, 12), (1, 6, 12, 12), (1, 36, 24, 24), (1, 2, 24, 24)]
SIZES = [int(np.prod(s)) for s in SHAPES]


def unflatten(row):
    out, off = [], 0
    for s, n in zip(SHAPES, SIZES):
        out.append(row[off:off + n].reshape(s))
        off += n
    return out


def assemble(parts):
    r16, c16, r8, c8 = parts
    rows = lambda t, k: t.transpose(0, 2, 3, 1).reshape(1, -1, k)
    a = [(rows(r8, 18), rows(c8, 1)), (rows(r16, 18), rows(c16, 1))]   # 8first
    return (np.concatenate([a[0][1], a[1][1]], axis=1),
            np.concatenate([a[0][0], a[1][0]], axis=1))


det = BlazeDetector("blazepalm")
det.in_shape = [1, 192, 192, 3]
det.out_reg_shape, det.out_clf_shape = [1, 2016, 18], [1, 2016, 1]
det.x_scale = det.y_scale = det.h_scale = det.w_scale = 192
det.num_anchors = 2016
det.config_model("blazepalm")
BASE, CLIP = det.min_score_thresh, det.score_clipping_thresh
calib = np.load("palm_calib_192.npy")


def peak(raw_scores):
    return float((1.0 / (1.0 + np.exp(-np.clip(raw_scores, -CLIP, CLIP)))).max())


for tag in sys.argv[1:]:
    flo = np.load("qpalm_%s_float.npy" % tag)
    i8 = np.load("qpalm_%s_int8.npy" % tag)
    pf = np.array([peak(assemble(unflatten(flo[i]))[0]) for i in range(len(calib))])
    pq = np.array([peak(assemble(unflatten(i8[i]))[0]) for i in range(len(calib))])
    pos, neg = pf >= BASE, pf < BASE

    print("\n=== %s ===   %d hand frames, %d no-hand frames" % (tag, pos.sum(), neg.sum()))
    print("  no-hand frames, float peak: median %.3f  max %.3f"
          % (np.median(pf[neg]), pf[neg].max()))
    print("  no-hand frames, int8  peak: median %.3f  max %.3f"
          % (np.median(pq[neg]), pq[neg].max()))
    print("  %-8s %-14s %-14s" % ("thresh", "recall", "false positives"))
    for t in (BASE, 0.4, 0.3, 0.2, 0.15, 0.1, 0.05):
        rec, fp = (pq[pos] >= t).sum(), (pq[neg] >= t).sum()
        print("  %-8.3f %3d/%-3d (%4.0f%%)  %3d/%-3d (%4.0f%%)"
              % (t, rec, pos.sum(), 100.0 * rec / pos.sum(),
                 fp, neg.sum(), 100.0 * fp / neg.sum()))
    # what float itself would do at the same threshold, so the cost of the
    # move is separated from the cost of quantising
    print("  float at the same thresholds, false positives:",
          ", ".join("%.2f:%d" % (t, (pf[neg] >= t).sum()) for t in (0.3, 0.2, 0.1, 0.05)))
