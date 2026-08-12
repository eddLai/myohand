"""Test whether keeping the world head in float would rescue the quantised model.

All four outputs branch from one 672-wide feature vector, so the damage is in
one of two places. If the vector itself is too coarse once quantised, then no
amount of precision in the head that reads it will help. If the vector is fine
and only the world head's own arithmetic was too coarse, then leaving that one
layer - under one percent of the compute - in float on the CPU recovers
everything while the rest still runs on the DPU.

The two are told apart by cutting the graph at the vector, quantising only what
comes before it, and applying the original float head to the result. Standard
int8 is used rather than the Vitis scheme because it is the more favourable of
the two: a failure here rules the approach out for both.

    python3 split_head_test.py model.onnx calib.npy
"""

import os
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import helper, numpy_helper
from onnxruntime.quantization import CalibrationDataReader, QuantType, quantize_static

sys.path.insert(0, "/work/onnx_route")
import hand_mapping as hm

SRC = sys.argv[1] if len(sys.argv) > 1 else "hl11_sim.onnx"
CAL = sys.argv[2] if len(sys.argv) > 2 else "calib_crops.npy"
FEAT = "model_1/model/global_average_pooling2d/Mean_Squeeze__594:0"
crops = np.load(CAL)
N = min(48, len(crops))
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")

model = onnx.load(SRC)
init = {t.name: t for t in model.graph.initializer}
world = next(n for n in model.graph.node if n.output[0] == "Identity_3")
W = numpy_helper.to_array(init[world.input[1]]).astype(np.float64)
B = numpy_helper.to_array(init[world.input[2]]).astype(np.float64)
tb = [a.i for a in world.attribute if a.name == "transB"]
print("world 頭 W%s B%s transB=%s" % (list(W.shape), list(B.shape), tb))


def head(feat):
    w = W.T if (tb and tb[0]) else W
    return (feat.astype(np.float64) @ w + B).reshape(21, 3)


# a copy that stops at the shared feature vector
cut = onnx.load(SRC)
del cut.graph.output[:]
cut.graph.output.extend([helper.make_tensor_value_info(
    FEAT, onnx.TensorProto.FLOAT, [1, int(W.shape[0])])])
# per-channel weights need DequantizeLinear's axis attribute, which arrived in
# opset 13; this file was written at 11 for a converter that no longer matters
from onnx import version_converter
cut = version_converter.convert_version(cut, 13)
onnx.save(cut, "trunk.onnx")


class Reader(CalibrationDataReader):
    def __init__(self):
        self.it = iter([{"input_1": c[None]} for c in crops[:100]])

    def get_next(self):
        return next(self.it, None)


quantize_static("trunk.onnx", "trunk_int8.onnx", Reader(),
                weight_type=QuantType.QInt8, per_channel=True)
print("主幹已量化 -> trunk_int8.onnx")


def feats(path):
    s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    name = s.get_inputs()[0].name
    return np.stack([s.run(None, {name: c[None]})[0].ravel()
                     for c in crops[:N]])


def pts(a):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in a]


f_float, f_int8 = feats("trunk.onnx"), feats("trunk_int8.onnx")
rel = np.abs(f_float - f_int8).max(1) / np.maximum(np.abs(f_float).max(1), 1e-9)
print("672 維特徵 量化後相對偏差 中位 %.2e  p95 %.2e"
      % (np.median(rel), np.percentile(rel, 95)))

base = [hm.raw_features(pts(head(f))) for f in f_float]
got = [hm.raw_features(pts(head(f))) for f in f_int8]
cpd = (1850 - 1034) / (hm.THUMB_CLOSED - hm.THUMB_OPEN)
print("\n量化主幹 + 浮點 world 頭，對全浮點的角度偏差（%d 張）" % N)
for k in KEYS:
    v = np.array([abs(a[k] - b[k]) % 360.0 for a, b in zip(base, got)])
    v = np.minimum(v, 360.0 - v)
    print("   %-9s 中位 %6.2f°  p95 %6.2f°  最大 %6.2f°  = %3.0f counts"
          % (k, np.median(v), np.percentile(v, 95), v.max(),
             np.median(v) * cpd))
print("\n對照  全部量化(標準 int8)：curl_lo 14.3° curl_hi 46.2° "
      "thumb 20.1° opp 48.7°")
print("      全部量化(Vitis)      ：curl_lo 34.4° curl_hi 101.0° "
      "thumb 77.8° opp 128.2°")
