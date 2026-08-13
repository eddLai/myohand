"""Drop the leading axis from every PReLU slope, and check nothing moved.

onnx2torch has a fast path for PReLU that calls torch native prelu, and a
fallback that clones the tensor and writes into it through a boolean mask.
The fallback is what XIR cannot read: it arrives as aten::clone, aten::ge and
nndct_index, and each one severs the convolutions either side of it, which is
how a 53-convolution model came out as 33 DPU subgraphs.

The fast path is guarded on slope.shape[0] == channels. These slopes ship as
(1, C, 1, 1), so the guard compares 1 against C and fails. ONNX broadcasts
(C, 1, 1) and (1, C, 1, 1) to the same thing, so the reshape is free, and it
is checked against onnxruntime here rather than assumed.

    python fix_prelu_shape.py <in.onnx> <out.onnx>
"""
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
init = {i.name: i for i in m.graph.initializer}

changed = 0
for n in m.graph.node:
    if n.op_type != "PRelu":
        continue
    t = init.get(n.input[1])
    if t is None:
        print("slope is not an initializer:", n.name[:50])
        continue
    a = numpy_helper.to_array(t)
    if a.ndim == 4 and a.shape[0] == 1:
        new = numpy_helper.from_array(a.reshape(a.shape[1:]), t.name)
        t.CopyFrom(new)
        changed += 1
print("reshaped %d slopes" % changed)
onnx.save(m, dst)

x = (np.load("palm_calib_192.npy")[:4].astype(np.float32) / 255.0)
a = ort.InferenceSession(src, providers=["CPUExecutionProvider"])
b = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
worst = 0.0
for i in range(len(x)):
    ra = a.run(None, {a.get_inputs()[0].name: x[i:i + 1]})
    rb = b.run(None, {b.get_inputs()[0].name: x[i:i + 1]})
    worst = max([worst] + [np.abs(p - q).max() for p, q in zip(ra, rb)])
print("outputs differ by at most %.3e over %d frames" % (worst, len(x)))
if worst > 0:
    raise SystemExit("the reshape was not free")
print("bit-exact")
