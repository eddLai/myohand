"""Put the standard-int8 model through the same angle test as the Vitis one.

Relative error on the world landmark tensor is hard to read: those coordinates
span about eight centimetres and the bones between them span two or three, so a
number that sounds tolerable on the tensor can be fatal on the joint it defines.
The comparison that decides anything is the one hand_mapping performs, and it is
the same one already run against the Vitis quantiser, which makes the two
directly comparable.

    python3 int8_angles.py float.tflite int8.tflite calib.npy
"""

import os
import sys

import numpy as np
from ai_edge_litert.interpreter import Interpreter

sys.path.insert(0, "/work/onnx_route")
import hand_mapping as hm

A = sys.argv[1] if len(sys.argv) > 1 else "/work/tf_route/saved_model/model_float32.tflite"
B = sys.argv[2] if len(sys.argv) > 2 else "/work/tf_route/int8.tflite"
CAL = sys.argv[3] if len(sys.argv) > 3 else "/work/onnx_route/calib_crops.npy"
crops = np.load(CAL)
N = min(48, len(crops))
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")


def loader(path):
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    return it


def world_of(it, x):
    d = it.get_input_details()[0]
    v = x if d["dtype"] == np.float32 else \
        (x / d["quantization"][0] + d["quantization"][1]).astype(d["dtype"])
    it.set_tensor(d["index"], v)
    it.invoke()
    best = None
    for o in it.get_output_details():
        a = it.get_tensor(o["index"]).astype(np.float32)
        if a.size != 63:
            continue
        if o["dtype"] != np.float32:
            s, z = o["quantization"]
            a = (a - z) * s
        # the metric skeleton is the 63-wide output whose values are metres,
        # not the one in crop pixels
        if best is None or np.abs(a).max() < np.abs(best).max():
            best = a
    return best.reshape(21, 3)


def pts(a):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in a]


fa, fb = loader(A), loader(B)
diff = {k: [] for k in KEYS}
for i in range(N):
    x = crops[i][None]
    a = hm.raw_features(pts(world_of(fa, x)))
    b = hm.raw_features(pts(world_of(fb, x)))
    for k in KEYS:
        d = abs(a[k] - b[k]) % 360.0
        diff[k].append(min(d, 360.0 - d))

cpd = (1850 - 1034) / (hm.THUMB_CLOSED - hm.THUMB_OPEN)
print("標準 int8 對浮點的角度偏差（%d 張）\n" % N)
for k in KEYS:
    v = np.array(diff[k])
    print("   %-9s 中位 %6.2f°  p95 %6.2f°  最大 %6.2f°   = %3.0f counts"
          % (k, np.median(v), np.percentile(v, 95), v.max(),
             np.median(v) * cpd))
print("\n對照 Vitis AI 的 2 的冪次量化：curl_lo 34.4° / curl_hi 101.0° / "
      "thumb 77.8° / opp 128.2°")
