"""What quantising the palm detector costs the crop it produces.

The tensor diffs printed by the quantiser are over 2016 anchors, almost all
of them background whose regressions nobody reads. The pipeline reads exactly
one thing from this model: where to cut the next 224x224 crop. So the honest
measure is how far that crop moves, in pixels of the frame it is cut from.

Decoding is the detector own, not a reimplementation, because anchor decode
and weighted NMS are where a second implementation would quietly differ. The
concatenation order of the two feature levels is not assumed either - both
orders are tried against the uncut graph and the matching one is used.

    python roi_delta.py
"""
import sys

import numpy as np
import onnxruntime as ort

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


def heads_to_rows(t, k):
    """NCHW head -> [1, anchors, k], the transpose+reshape that was cut off."""
    return t.transpose(0, 2, 3, 1).reshape(1, -1, k)


def assemble(parts, order):
    r16, c16, r8, c8 = parts
    a = [(heads_to_rows(r16, 18), heads_to_rows(c16, 1)),
         (heads_to_rows(r8, 18), heads_to_rows(c8, 1))]
    if order == "8first":
        a = a[::-1]
    return (np.concatenate([a[0][1], a[1][1]], axis=1),
            np.concatenate([a[0][0], a[1][0]], axis=1))


calib = np.load("palm_calib_192.npy")
flo = np.load("qpalm_full_float.npy")
i8 = np.load("qpalm_full_int8.npy")
print("frames", len(calib), "rows", flo.shape)

sess = ort.InferenceSession("pd11_sim.onnx", providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name
ref = sess.run(None, {iname: (calib[:1].astype(np.float32) / 255.0)})
ref_coords, ref_scores = ref[0], ref[1]

order = None
for cand in ("16first", "8first"):
    s, c = assemble(unflatten(flo[0]), cand)
    if np.abs(c - ref_coords).max() < 1e-3 and np.abs(s - ref_scores).max() < 1e-3:
        order = cand
        break
if order is None:
    for cand in ("16first", "8first"):
        s, c = assemble(unflatten(flo[0]), cand)
        print(cand, "coord err", np.abs(c - ref_coords).max(),
              "score err", np.abs(s - ref_scores).max())
    raise SystemExit("neither concat order reproduces the uncut graph")
print("concat order:", order)

det = BlazeDetector("blazepalm")
# load_model would set these from the tflite; the graph being measured is an
# onnx cut of it, so they are stated instead of read
det.in_shape = [1, 192, 192, 3]
det.out_reg_shape, det.out_clf_shape = [1, 2016, 18], [1, 2016, 1]
det.x_scale = det.y_scale = det.h_scale = det.w_scale = 192
det.num_anchors = 2016
det.config_model("blazepalm")


def roi_of(parts):
    scores, coords = assemble(parts, order)
    det.predict_core = lambda x: (scores, coords)
    d = det.predict_on_batch(calib[:1].astype(np.float32) / 255.0)
    if not len(d) or not len(d[0]):
        return None
    return det.detection2roi(np.asarray(d[0]))


rows = []
for i in range(len(calib)):
    det_in = calib[i:i + 1].astype(np.float32) / 255.0
    a, b = None, None
    for tag, arr in (("f", flo), ("q", i8)):
        scores, coords = assemble(unflatten(arr[i]), order)
        det.predict_core = lambda x, s=scores, c=coords: (s, c)
        d = det.predict_on_batch(det_in)
        r = det.detection2roi(np.asarray(d[0])) if len(d) and len(d[0]) else None
        if tag == "f":
            a = r
        else:
            b = r
    if a is None or b is None:
        rows.append((np.nan, np.nan, np.nan, a is None, b is None))
        continue
    xa, ya, sa, ta = [np.asarray(v).ravel()[0] for v in a]
    xb, yb, sb, tb = [np.asarray(v).ravel()[0] for v in b]
    rows.append((np.hypot(xa - xb, ya - yb), abs(sa - sb),
                 abs(np.degrees(ta - tb)), False, False))

r = np.array([x[:3] for x in rows], dtype=float)
missf = sum(1 for x in rows if x[3])
missq = sum(1 for x in rows if x[4])
ok = ~np.isnan(r[:, 0])
print()
print("frames with a detection    float %d   int8 %d   both %d"
      % (len(calib) - missf, len(calib) - missq, ok.sum()))
print()
print("%-22s %8s %8s %8s" % ("", "median", "p90", "worst"))
for k, name in enumerate(("crop centre  (px)", "crop side    (px)", "crop angle  (deg)")):
    v = r[ok, k]
    print("%-22s %8.2f %8.2f %8.2f" % (name, np.median(v), np.percentile(v, 90), v.max()))
