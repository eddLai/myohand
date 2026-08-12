"""Check that the round-tripped model still computes what the original did.

The model has been through tflite to ONNX to TensorFlow, and each hop is a
chance for a layout to be transposed, a padding to be reinterpreted, or an
output to be reordered. None of those announce themselves - the graph still
loads, the shapes still match, and the numbers are quietly wrong. Since
everything after this point is built on the converted model, the cheapest place
to catch that is here, on identical inputs, before any quantization muddies the
comparison.

Outputs are matched by content rather than by position because the converters
reorder them.

    python3 roundtrip_check.py original.tflite converted.tflite
"""

import sys

import numpy as np
from ai_edge_litert.interpreter import Interpreter

a_path, b_path = sys.argv[1], sys.argv[2]
rng = np.random.default_rng(0)


def run(path, x):
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    d = it.get_input_details()[0]
    it.set_tensor(d["index"], x.astype(d["dtype"]))
    it.invoke()
    return [it.get_tensor(o["index"]) for o in it.get_output_details()], d["shape"]


probe = Interpreter(model_path=a_path)
probe.allocate_tensors()
shape = probe.get_input_details()[0]["shape"]
x = rng.random(tuple(shape), dtype=np.float32)

A, sa = run(a_path, x)
B, sb = run(b_path, x)
print("輸入 %s vs %s" % (list(sa), list(sb)))
print("輸出數量 %d vs %d\n" % (len(A), len(B)))

used = set()
worst = 0.0
for i, a in enumerate(A):
    best, bj = None, None
    for j, b in enumerate(B):
        if j in used or b.shape != a.shape:
            continue
        e = float(np.abs(a - b).max())
        if best is None or e < best:
            best, bj = e, j
    if bj is None:
        print("out%-2d %-12s 找不到形狀相符的對應" % (i, str(a.shape)))
        worst = float("inf")
        continue
    used.add(bj)
    rng_ = float(np.abs(a).max()) or 1.0
    worst = max(worst, best / rng_)
    print("out%-2d %-12s -> 轉檔的 out%-2d   最大差 %.3e  (值域 %.3f, 相對 %.2e)"
          % (i, str(a.shape), bj, best, rng_, best / rng_))

print("\n" + ("轉檔忠實，可以往下做。" if worst < 1e-4 else
              "⚠ 相對誤差 %.2e —— 轉檔過程改到了東西，往下做會累積。" % worst))
