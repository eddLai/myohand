#!/usr/bin/env python3
"""Build the palm detector's calibration set from the reference recording.

The landmark set could be cropped hands because that is what the landmark
model is ever shown. The palm detector is shown whole frames, letterboxed,
so a calibration set of crops would describe a distribution the model never
sees in service and would quantise the wrong range.

resize_pad comes from the deployed detector rather than being reimplemented
here, because the padding rule is the thing that decides where the hand
lands in the tensor, and a second implementation of it is a second thing
that can drift.

    python3 make_palm_calib.py [out.npy]
"""
import os
import sys

import numpy as np

# from this file rather than from a home directory: the detector and the
# recording both live in this repository, and nn/dpu/palm is three levels down
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CAMERA = os.path.join(REPO, "camera")
sys.path.insert(0, CAMERA)
sys.path.insert(0, os.path.join(CAMERA, "blaze"))
import hand_pipeline as hp
from blazedetector import BlazeDetector

# where ref_capture.py puts it; override for a recording kept elsewhere
NPZ = os.environ.get("REFCAP_NPZ",
                     os.path.join(REPO, "nn", "refcap", "frames.npz"))
OUT = sys.argv[1] if len(sys.argv) > 1 else "palm_calib_192.npy"

if not os.path.exists(NPZ):
    raise SystemExit(
        "no recording at %s\n"
        "Record one first:  python3 nn/ref_capture.py\n"
        "or point REFCAP_NPZ at an existing frames.npz." % NPZ)

# the weights the pipeline itself resolves, so the calibration set cannot end
# up describing a different copy of the model than the one being quantised
palm, _ = hp.default_models()
print("model:", palm)
det = BlazeDetector("blazepalm")
det.load_model(palm)
print("detector input:", det.in_shape)

frames = np.load(NPZ)["frames"]
out = np.empty((len(frames),) + tuple(det.in_shape[1:]), dtype=np.uint8)
for i, f in enumerate(frames):
    img, scale, pad = det.resize_pad(f[:, :, ::-1])
    out[i] = img

np.save(OUT, out)
print(f"{OUT}: {out.shape} {out.dtype}, {out.nbytes / 1e6:.1f} MB")
print(f"pixel range {out.min()}..{out.max()}, mean {out.mean():.1f}")
