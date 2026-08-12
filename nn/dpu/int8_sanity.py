"""Separate "this model cannot be quantised" from "this quantiser cannot do it".

Vitis AI restricts activation scales to powers of two, because the DPU rescales
by shifting rather than multiplying. That is far coarser than ordinary int8,
and depthwise convolutions - which this model is largely built from - have
per-channel ranges that vary enough for the difference to matter. So a collapse
under the Vitis quantiser is not by itself evidence that the weights cannot
survive eight bits.

TensorFlow's converter quantises the same graph on the same crops without that
restriction. If it holds up, the loss is the scheme rather than the model, and
the remedies are different ones.

    python3 int8_sanity.py saved_model_dir calib.npy
"""

import os
import sys

import numpy as np
import tensorflow as tf
from ai_edge_litert.interpreter import Interpreter

SM = sys.argv[1] if len(sys.argv) > 1 else "/work/tf_route/saved_model"
CAL = sys.argv[2] if len(sys.argv) > 2 else "/work/onnx_route/calib_crops.npy"
crops = np.load(CAL)
N = min(48, len(crops))


def rep():
    for c in crops[:100]:
        yield [c[None]]


conv = tf.lite.TFLiteConverter.from_saved_model(SM)
conv.optimizations = [tf.lite.Optimize.DEFAULT]
conv.representative_dataset = rep
q = conv.convert()
open("/work/tf_route/int8.tflite", "wb").write(q)
print("int8 tflite %d bytes" % len(q))


def run(path, x):
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    d = it.get_input_details()[0]
    v = x if d["dtype"] == np.float32 else \
        (x / d["quantization"][0] + d["quantization"][1]).astype(d["dtype"])
    it.set_tensor(d["index"], v)
    it.invoke()
    out = []
    for o in it.get_output_details():
        a = it.get_tensor(o["index"]).astype(np.float32)
        if o["dtype"] != np.float32:
            s, z = o["quantization"]
            a = (a - z) * s
        out.append(a.ravel())
    return out


base = "/work/tf_route/saved_model/model_float32.tflite"
errs = {}
for i in range(N):
    a = run(base, crops[i][None])
    b = run("/work/tf_route/int8.tflite", crops[i][None])
    for k, (x, y) in enumerate(zip(a, b)):
        if x.size != y.size:
            continue
        errs.setdefault(k, []).append((np.abs(x - y).max(), np.abs(x).max()))

print("\n標準 int8（非 2 的冪次）vs 浮點，%d 張" % N)
for k, v in sorted(errs.items()):
    d = np.array([e for e, _ in v])
    r = np.array([m for _, m in v])
    print("   輸出 %d  大小 %-5d 最大差 中位 %.4f   值域 %.3f   相對 %.2e"
          % (k, len(run(base, crops[:1])[k]), np.median(d), np.median(r),
             np.median(d) / max(np.median(r), 1e-9)))
