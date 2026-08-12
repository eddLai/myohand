"""Quantise the landmark model on real crops and export it as an xmodel.

The inspector said where each operator would run; it did not say what the model
answers once its weights and activations are held to 8 bits. That depends on the
ranges seen during calibration, which is why the crops fed here are the ones the
pipeline produces rather than random tensors or raw camera frames.

Two passes are required by the quantiser: the first only observes ranges, the
second replays them and can write the xmodel. Both are run here, and the float
and quantised answers are compared on the same crops afterwards - a model that
compiles but has drifted is worse than one that fails to compile, because
nothing reports it.

    python3 quantize_xmodel.py model.onnx calib.npy DPUCZDX8G_ISA1_B4096 out_dir
"""

import os
import sys

import numpy as np
import torch
from onnx2torch import convert
from pytorch_nndct.apis import torch_quantizer

onnx_path, calib_path = sys.argv[1], sys.argv[2]
arch = sys.argv[3] if len(sys.argv) > 3 else "DPUCZDX8G_ISA1_B4096"
out = sys.argv[4] if len(sys.argv) > 4 else "q_" + arch.split("_")[-1]

crops = np.load(calib_path)
print("校正資料 %s  值域 %.3f-%.3f" % (crops.shape, crops.min(), crops.max()))
model = convert(onnx_path).eval()

with torch.no_grad():
    ref = [model(torch.from_numpy(c[None])) for c in crops[:16]]


def run(qmodel, n):
    outs = []
    with torch.no_grad():
        for c in crops[:n]:
            outs.append(qmodel(torch.from_numpy(c[None])))
    return outs


for mode in ("calib", "test"):
    # the test pass must run a single batch before the xmodel can be written
    n = len(crops) if mode == "calib" else 1
    q = torch_quantizer(mode, model, (torch.from_numpy(crops[:1]),),
                        device=torch.device("cpu"), target=arch,
                        output_dir=out)
    outs = run(q.quant_model, n)
    if mode == "calib":
        q.export_quant_config()
        print("校正完成，%d 張" % n)
    else:
        q.export_xmodel(output_dir=out, deploy_check=False)
        print("xmodel 已輸出")

# how far the 8-bit answer moved, on the tensors the joint angles are built from
qmodel = q.quant_model
with torch.no_grad():
    got = [qmodel(torch.from_numpy(c[None])) for c in crops[:16]]
for k in range(len(ref[0])):
    a = np.stack([np.asarray(r[k]).ravel() for r in ref])
    b = np.stack([np.asarray(g[k]).ravel() for g in got])
    rng = np.abs(a).max() or 1.0
    print("輸出 %d  形狀 %-10s 最大差 %.4f  值域 %.3f  相對 %.2e"
          % (k, str(np.asarray(ref[0][k]).shape), np.abs(a - b).max(), rng,
             np.abs(a - b).max() / rng))

print("\n輸出目錄：%s" % out)
for f in sorted(os.listdir(out)):
    print("   " + f)
