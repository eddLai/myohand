"""Quantise the palm detector and say what it costs where it is read.

The landmark model failed here, and it failed for a reason specific to what it
outputs: a metric skeleton eight centimetres across, whose bones are two to
three. The palm detector outputs a box and seven keypoints in the frame, which
the pipeline turns into a crop. So the question is not how far the tensors
moved but how far the crop moved, because a crop that lands in the same place
feeds the landmark model the same picture and nothing downstream can tell.

Anchors are decoded here rather than compared raw for the same reason: 2016
raw regressions mean nothing to anyone, and the two numbers that matter are
the centre of the crop and its side, in pixels of a 192-wide input.

    python quant_palm.py <onnx> <calib.npy> [arch] [outdir]
"""
import sys

import numpy as np
import torch
from onnx2torch import convert
from pytorch_nndct.apis import torch_quantizer

onnx_path = sys.argv[1]
calib_path = sys.argv[2]
arch = sys.argv[3] if len(sys.argv) > 3 else "DPUCZDX8G_ISA1_B4096"
out = sys.argv[4] if len(sys.argv) > 4 else "qpalm_" + arch.split("_")[-1]

raw = np.load(calib_path)
x = raw.astype(np.float32) / 255.0        # the graph is NHWC; its own transpose follows
print("calib %s  range %.3f-%.3f" % (str(x.shape), x.min(), x.max()))

model = convert(onnx_path).eval()
try:
    import swap_prelu
    n = swap_prelu.swap(model)
    print("swapped %d PReLU calls for nn.PReLU" % n)
except Exception as e:
    print("PReLU swap skipped:", e)
with torch.no_grad():
    ref = [model(torch.from_numpy(x[i:i + 1])) for i in range(len(x))]
print("float outputs:", [tuple(t.shape) for t in ref[0]])

for mode in ("calib", "test"):
    n = len(x) if mode == "calib" else 1
    q = torch_quantizer(mode, model, (torch.from_numpy(x[:1]),),
                        device=torch.device("cpu"), target=arch, output_dir=out)
    with torch.no_grad():
        for i in range(n):
            q.quant_model(torch.from_numpy(x[i:i + 1]))
    if mode == "calib":
        q.export_quant_config()
        print("calibrated on %d frames" % n)
    else:
        q.export_xmodel(output_dir=out, deploy_check=False)
        print("xmodel written to", out)

with torch.no_grad():
    got = [q.quant_model(torch.from_numpy(x[i:i + 1])) for i in range(len(x))]

for k in range(len(ref[0])):
    a = np.stack([r[k].numpy().ravel() for r in ref])
    b = np.stack([g[k].numpy().ravel() for g in got])
    rng = np.abs(a).max() or 1.0
    print("output %d  shape %-16s max diff %.4f  range %.3f  relative %.2e"
          % (k, str(tuple(ref[0][k].shape)), np.abs(a - b).max(), rng,
             np.abs(a - b).max() / rng))

np.save(out + "_float.npy", np.stack([np.concatenate([t.numpy().ravel() for t in r]) for r in ref]))
np.save(out + "_int8.npy", np.stack([np.concatenate([t.numpy().ravel() for t in g]) for g in got]))
print("saved raw outputs for ROI decoding")
