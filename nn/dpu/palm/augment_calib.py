"""Widen the calibration set without asking anyone to stand in front of a camera.

Rewriting PReLU into five operators multiplied the number of tensors whose
range has to be observed, while the calibration set stayed at the 150 frames
the reference recording holds. The detector kept its geometry - the crop still
lands within a hundredth of a pixel - but lost detections, 86 of 117 before
the rewrite and 73 after, which is the signature of ranges estimated from too
few samples rather than of a wrong graph.

The augmentations are the ones the pipeline itself can produce: the operator
faces the camera with either hand, the room is not always lit the same, and
the hand is not always the same distance away. Nothing here invents a pose
the model would not see, because calibration is meant to describe the input
distribution rather than to enlarge it.

    python augment_calib.py <in.npy> <out.npy> [target]
"""
import sys

import cv2
import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else "palm_calib_192.npy"
dst = sys.argv[2] if len(sys.argv) > 2 else "palm_calib_aug.npy"
target = int(sys.argv[3]) if len(sys.argv) > 3 else 1050

base = np.load(src)
n, h, w, _ = base.shape
rng = np.random.RandomState(0)
out = [base]

# mirroring is the one augmentation the deployment actually performs: teleop
# flips the frame, and either hand can be the one held up
out.append(base[:, :, ::-1])

while sum(len(a) for a in out) < target:
    k = len(out)
    gain = 0.75 + 0.1 * (k % 6)                 # room lighting
    zoom = 1.0 + 0.06 * ((k % 5) - 2)           # distance to the camera
    batch = np.empty_like(base)
    for i, f in enumerate(base):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0.0, zoom)
        z = cv2.warpAffine(f, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
        batch[i] = np.clip(z.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    out.append(batch if k % 2 else batch[:, :, ::-1])

a = np.concatenate(out)[:target]
idx = rng.permutation(len(a))          # calibration reads them in order
a = a[idx]
np.save(dst, a)
print("%s: %s %s, %.1f MB" % (dst, a.shape, a.dtype, a.nbytes / 1e6))
print("built from %d recorded frames, %.1fx" % (n, len(a) / n))
print("pixel mean %.1f (source %.1f), range %d..%d"
      % (a.mean(), base.mean(), a.min(), a.max()))
