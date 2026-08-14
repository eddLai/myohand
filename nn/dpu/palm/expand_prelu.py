"""Rewrite every PReLU into operators the DPU already runs.

PReLU is not on DPUCZDX8G menu, so all 26 of them land on the CPU and cut the
convolution chain into 33 pieces. The identity

    PReLU(x) = ReLU(x) - a * ReLU(-x)

is exact rather than approximate, and every term on the right is something
the compiled graph was already running on the DPU today: ReLU, a per-channel
multiply expressed as a 1x1 depthwise convolution, and an elementwise add.

The negation and the -a are each folded into a depthwise weight so no Neg or
Sub is needed, which leaves five operators per activation and no reason for
any of them to leave the device.

    python expand_prelu.py <in.onnx> <out.onnx>
"""
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
g = m.graph
init = {i.name: i for i in g.initializer}


def dwconv(name, x, out, w):
    """A 1x1 depthwise convolution, i.e. multiply each channel by its own w."""
    c = w.size
    wt = numpy_helper.from_array(w.reshape(c, 1, 1, 1).astype(np.float32), name + "_w")
    g.initializer.append(wt)
    return helper.make_node("Conv", [x, wt.name], [out], name=name, group=c,
                            kernel_shape=[1, 1], strides=[1, 1], pads=[0, 0, 0, 0])


new_nodes, done = [], 0
for n in g.node:
    if n.op_type != "PRelu":
        new_nodes.append(n)
        continue
    t = init.get(n.input[1])
    if t is None:
        new_nodes.append(n)
        continue
    a = numpy_helper.to_array(t).ravel()
    x, y, b = n.input[0], n.output[0], n.name or ("prelu_%d" % done)
    new_nodes += [
        helper.make_node("Relu", [x], [b + "/pos"], name=b + "/pos"),
        dwconv(b + "/neg", x, b + "/negx", -np.ones_like(a)),
        helper.make_node("Relu", [b + "/negx"], [b + "/negr"], name=b + "/negr"),
        dwconv(b + "/scale", b + "/negr", b + "/negs", -a),
        helper.make_node("Add", [b + "/pos", b + "/negs"], [y], name=b + "/sum"),
    ]
    done += 1

del g.node[:]
g.node.extend(new_nodes)
onnx.checker.check_model(m)
onnx.save(m, dst)
print("expanded %d PReLU into %d operators" % (done, done * 5))

x = np.load("palm_calib_192.npy")[:6].astype(np.float32) / 255.0
a = ort.InferenceSession(src, providers=["CPUExecutionProvider"])
b = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
worst = 0.0
for i in range(len(x)):
    ra = a.run(None, {a.get_inputs()[0].name: x[i:i + 1]})
    rb = b.run(None, {b.get_inputs()[0].name: x[i:i + 1]})
    worst = max([worst] + [np.abs(p - q).max() for p, q in zip(ra, rb)])
print("outputs differ by at most %.3e over %d frames" % (worst, len(x)))
