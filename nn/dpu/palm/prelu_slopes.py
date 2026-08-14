"""What the 26 PReLU slopes actually are, before deciding to replace them.

The compiled graph broke into 33 pieces because onnx2torch expands PReLU into
operators XIR has never heard of, and each one severs the convolutions either
side of it. LeakyReLU is native to the DPU, so the substitution is available -
but only if the per-channel slopes are close enough to a single number that
replacing them changes the answer less than quantisation already does.

Reported per layer rather than pooled, because one ill-behaved layer is a
different problem from a model that is diverse throughout.

    python prelu_slopes.py <model.onnx>
"""
import sys

import numpy as np
import onnx
from onnx import numpy_helper

m = onnx.load(sys.argv[1] if len(sys.argv) > 1 else "pd11_sim.onnx")
init = {i.name: numpy_helper.to_array(i) for i in m.graph.initializer}

rows = []
for n in m.graph.node:
    if n.op_type != "PRelu":
        continue
    s = init.get(n.input[1])
    if s is None:
        rows.append((n.name, None))
        continue
    rows.append((n.name, s.ravel()))

print("%-4s %6s %9s %9s %9s %9s %9s" % ("#", "chans", "min", "median", "max", "std", "spread"))
allv = []
for i, (name, s) in enumerate(rows):
    if s is None:
        print("%-4d  slope is not a constant" % i)
        continue
    allv.append(s)
    print("%-4d %6d %9.4f %9.4f %9.4f %9.4f %9.4f"
          % (i, s.size, s.min(), np.median(s), s.max(), s.std(), s.max() - s.min()))

v = np.concatenate(allv)
print()
print("all %d slopes: min %.4f  median %.4f  max %.4f  std %.4f"
      % (v.size, v.min(), np.median(v), v.max(), v.std()))
print("fraction within +-0.05 of the global median: %.1f%%"
      % (100 * np.mean(np.abs(v - np.median(v)) <= 0.05)))
print("fraction negative: %.1f%%   fraction above 1: %.1f%%"
      % (100 * np.mean(v < 0), 100 * np.mean(v > 1)))
