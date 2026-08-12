"""Ask the Vitis-AI inspector where each operator would run, before quantizing.

Quantization needs calibration data and calibration data needs the cropping
pipeline that does not exist yet, so the expensive question - how accurate is
the quantized model - cannot be answered today. The cheap question can: whether
this graph stays in one DPU subgraph or gets cut into pieces by an operator the
hardware does not implement. A cut in the middle costs a round trip to the CPU
per frame and is what decides whether this route is worth pursuing at all.

The inspector answers it from the graph alone.

    python3 inspect_dpu.py model.onnx DPUCZDX8G_ISA1_B4096 1,224,224,3
"""

import collections
import os
import sys

import torch
from onnx2torch import convert
from pytorch_nndct.apis import Inspector

onnx_path = sys.argv[1]
arch = sys.argv[2] if len(sys.argv) > 2 else "DPUCZDX8G_ISA1_B4096"
shape = tuple(int(x) for x in sys.argv[3].split(",")) if len(sys.argv) > 3 \
    else (1, 224, 224, 3)
out = os.path.splitext(os.path.basename(onnx_path))[0] + "_" + arch.split("_")[-1]

model = convert(onnx_path).eval()
x = torch.randn(*shape)
with torch.no_grad():
    y = model(x)
ys = y if isinstance(y, (list, tuple)) else [y]
print("torch forward ok ->", [tuple(t.shape) for t in ys])

Inspector(arch).inspect(model, (x,), device=torch.device("cpu"), output_dir=out,
                        image_format="png")

# the inspector writes its verdict per node; count how the graph got divided
txt = os.path.join(out, "inspect_" + arch + ".txt")
if not os.path.exists(txt):
    cand = [f for f in os.listdir(out) if f.endswith(".txt")]
    txt = os.path.join(out, cand[0]) if cand else None
if txt:
    body = open(txt, encoding="utf-8", errors="replace").read()
    dev = collections.Counter()
    for line in body.splitlines():
        if "device=" in line:
            dev[line.split("device=")[1].split(",")[0].strip()] += 1
    print("\n%s  節點分派" % arch)
    for k, v in dev.most_common():
        print("   %-6s %d" % (k, v))
    print("報告：%s" % txt)
