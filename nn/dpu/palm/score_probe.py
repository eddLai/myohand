"""Why quantisation loses detections, when it does not lose the crop.

roi_delta showed the surviving crops land within 0.03 px of float, so the
regression head is intact and the loss is entirely in confidence. Two things
could produce that: every score shifted down by roughly the same amount, in
which case the threshold is simply in the wrong place for int8 and moving it
costs nothing; or the scores scrambled, in which case a lower threshold buys
detections at the price of false ones and the head needs requantising.

This tells the two apart, by asking what the missed frames actually scored
and whether the crop that comes back at a lower threshold is the right crop.

    python score_probe.py <tag> [tag ...]
"""
import sys

import numpy as np

sys.path.insert(0, ".")
from blazedetector import BlazeDetector

SHAPES = [(1, 108, 12, 12), (1, 6, 12, 12), (1, 36, 24, 24), (1, 2, 24, 24)]
SIZES = [int(np.prod(s)) for s in SHAPES]
ORDER = "8first"          # established by roi_delta against the uncut graph


def unflatten(row):
    out, off = [], 0
    for s, n in zip(SHAPES, SIZES):
        out.append(row[off:off + n].reshape(s))
        off += n
    return out


def heads_to_rows(t, k):
    return t.transpose(0, 2, 3, 1).reshape(1, -1, k)


def assemble(parts):
    r16, c16, r8, c8 = parts
    a = [(heads_to_rows(r16, 18), heads_to_rows(c16, 1)),
         (heads_to_rows(r8, 18), heads_to_rows(c8, 1))]
    if ORDER == "8first":
        a = a[::-1]
    return (np.concatenate([a[0][1], a[1][1]], axis=1),
            np.concatenate([a[0][0], a[1][0]], axis=1))


det = BlazeDetector("blazepalm")
det.in_shape = [1, 192, 192, 3]
det.out_reg_shape, det.out_clf_shape = [1, 2016, 18], [1, 2016, 1]
det.x_scale = det.y_scale = det.h_scale = det.w_scale = 192
det.num_anchors = 2016
det.config_model("blazepalm")
BASE = det.min_score_thresh
CLIP = det.score_clipping_thresh
print("min_score_thresh %.3f   score_clipping_thresh %.1f" % (BASE, CLIP))

calib = np.load("palm_calib_192.npy")


def best_score(raw_scores):
    """Peak confidence over anchors, decoded the way the detector decodes it."""
    s = np.clip(raw_scores, -CLIP, CLIP)
    return float((1.0 / (1.0 + np.exp(-s))).max())


def roi_at(raw, i, thresh):
    det.min_score_thresh = thresh
    scores, coords = assemble(unflatten(raw[i]))
    det.predict_core = lambda x, s=scores, c=coords: (s, c)
    d = det.predict_on_batch(calib[i:i + 1].astype(np.float32) / 255.0)
    if not len(d) or not len(d[0]):
        return None
    return [float(np.asarray(v).ravel()[0]) for v in det.detection2roi(np.asarray(d[0]))]


for tag in sys.argv[1:]:
    flo = np.load("qpalm_%s_float.npy" % tag)
    i8 = np.load("qpalm_%s_int8.npy" % tag)
    print("\n=== %s ===" % tag)

    pf = np.array([best_score(assemble(unflatten(flo[i]))[0]) for i in range(len(calib))])
    pq = np.array([best_score(assemble(unflatten(i8[i]))[0]) for i in range(len(calib))])

    det_f = pf >= BASE
    lost = det_f & (pq < BASE)
    print("float above threshold %d / %d" % (det_f.sum(), len(calib)))
    print("int8  above threshold %d" % (pq >= BASE).sum())
    print("lost  %d frames" % lost.sum())
    if lost.sum():
        print("  their float peak  median %.3f  min %.3f" % (np.median(pf[lost]), pf[lost].min()))
        print("  their int8  peak  median %.3f  max %.3f" % (np.median(pq[lost]), pq[lost].max()))
    kept = det_f & (pq >= BASE)
    if kept.sum():
        print("  kept frames, int8 peak / float peak: median %.3f"
              % np.median(pq[kept] / pf[kept]))

    # a shift moves every score by the same factor; a scramble does not
    ok = pf > 0.01
    print("  ratio int8/float over all detected frames: "
          "median %.3f  p10 %.3f  p90 %.3f"
          % (np.median(pq[ok] / pf[ok]), np.percentile(pq[ok] / pf[ok], 10),
             np.percentile(pq[ok] / pf[ok], 90)))

    print("  threshold sweep (int8 detections, and how far the crop moves):")
    for t in (BASE, 0.3, 0.2, 0.1, 0.05, 0.02):
        n, dists = 0, []
        for i in range(len(calib)):
            if not det_f[i]:
                continue
            a = roi_at(flo, i, BASE)
            b = roi_at(i8, i, t)
            if b is None:
                continue
            n += 1
            if a is not None:
                dists.append(np.hypot(a[0] - b[0], a[1] - b[1]))
        d = np.array(dists) if dists else np.array([np.nan])
        print("    thresh %.3f -> %3d/%d detected, crop shift median %.2f px  worst %.2f px"
              % (t, n, det_f.sum(), np.median(d), np.max(d)))
    det.min_score_thresh = BASE
