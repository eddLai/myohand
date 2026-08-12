"""Measure what 8-bit quantisation costs, in degrees rather than in tensors.

A relative error on an output tensor is not actionable: the landmark model emits
a metric skeleton, and what the robot hand receives is a set of joint angles
derived from it. Two skeletons can differ noticeably and produce the same
angles, or agree closely and not. So the float and quantised models are run on
the same crops and both skeletons are pushed through hand_mapping, which is the
code that will consume them.

Plain post-training calibration is compared against fast finetune, the
quantiser's layer-wise correction pass, because a first pass that looks bad is
not yet evidence that the model cannot be quantised - only that the cheap method
was not enough.

    python3 eval_quant.py model.onnx calib.npy arch
"""

import os
import sys

import numpy as np
import torch
from onnx2torch import convert
from pytorch_nndct.apis import torch_quantizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hand_mapping as hm

onnx_path = sys.argv[1] if len(sys.argv) > 1 else "hl11_sim.onnx"
calib_path = sys.argv[2] if len(sys.argv) > 2 else "calib_crops.npy"
arch = sys.argv[3] if len(sys.argv) > 3 else "DPUCZDX8G_ISA1_B4096"
FT = int(sys.argv[4]) if len(sys.argv) > 4 else 32     # crops used for finetune

crops = np.load(calib_path)
model = convert(onnx_path).eval()
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")


def pts(a):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in a]


def world_of(outs):
    """Output 3 is the metric skeleton; index by shape so order cannot bite."""
    for o in outs:
        a = np.asarray(o.detach() if hasattr(o, "detach") else o)
        if a.size == 63:
            yield a.reshape(21, 3)


def angles(m, n):
    got = []
    with torch.no_grad():
        for c in crops[:n]:
            outs = m(torch.from_numpy(c[None]))
            ws = list(world_of(outs))
            got.append(hm.raw_features(pts(ws[-1])))
    return got


N = min(48, len(crops))
base = angles(model, N)
print("浮點基準 %d 張" % N)


def evaluate(m, *a):
    with torch.no_grad():
        for c in crops[:FT]:
            m(torch.from_numpy(c[None]))
    return 0.0


def score(name, out_dir, finetune):
    q = torch_quantizer("calib", model, (torch.from_numpy(crops[:1]),),
                        device=torch.device("cpu"), target=arch,
                        output_dir=out_dir)
    if finetune:
        q.fast_finetune(evaluate, (q.quant_model,))
    else:
        with torch.no_grad():
            for c in crops:
                q.quant_model(torch.from_numpy(c[None]))
    q.export_quant_config()

    t = torch_quantizer("test", model, (torch.from_numpy(crops[:1]),),
                        device=torch.device("cpu"), target=arch,
                        output_dir=out_dir)
    if finetune:
        t.load_ft_param()
    got = angles(t.quant_model, N)
    print("\n%s" % name)
    for k in KEYS:
        v = np.array([abs(a[k] - b[k]) for a, b in zip(base, got)])
        v = np.minimum(v % 360.0, 360.0 - v % 360.0)
        print("   %-9s 中位 %7.2f°  p95 %7.2f°  最大 %7.2f°"
              % (k, np.median(v), np.percentile(v, 95), v.max()))
    return t


score("一般 PTQ", "q_plain", False)
t = score("fast_finetune（%d 張）" % FT, "q_ft", True)
t.export_xmodel(output_dir="q_ft", deploy_check=False)
print("\nfast_finetune 版 xmodel 已輸出到 q_ft")
