"""Replace channel zero-padding with the 1x1 convolution that does the same.

Three Pad operators remain on the CPU after the PReLU rewrite, and they are
the only reason the graph is still in six pieces rather than one. They are
not spatial padding: each appends C zero channels so a residual branch can be
added to a wider trunk, which a 1x1 convolution expresses exactly - identity
rows for the channels that carry through, zero rows for the ones being
introduced.

The DPU runs convolutions and will not run CONSTANT-mode padding, so the
substitution costs one cheap operator and buys back the partition.

    python expand_pad.py <in.onnx> <out.onnx>
"""
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import helper, numpy_helper

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
g = m.graph
init = {i.name: i for i in g.initializer}
shape = {v.name: [d.dim_value for d in v.type.tensor_type.shape.dim]
         for v in list(g.value_info) + list(g.input) + list(g.output)}

new_nodes, done = [], 0
for n in g.node:
    if n.op_type != "Pad" or len(n.input) < 2 or n.input[1] not in init:
        new_nodes.append(n)
        continue
    pads = numpy_helper.to_array(init[n.input[1]])
    spatial = np.concatenate([pads[:1], pads[2:4], pads[4:5], pads[6:]])
    if pads.size != 8 or spatial.any() or pads[5] <= 0:
        new_nodes.append(n)          # not a pure channel pad; leave it alone
        continue
    cin = shape.get(n.input[0], [0, 0])[1]
    cout = cin + int(pads[5])
    w = np.zeros((cout, cin, 1, 1), dtype=np.float32)
    w[np.arange(cin), np.arange(cin)] = 1.0
    wt = numpy_helper.from_array(w, n.name + "_w")
    g.initializer.append(wt)
    new_nodes.append(helper.make_node("Conv", [n.input[0], wt.name], [n.output[0]],
                                      name=n.name + "/asconv", group=1,
                                      kernel_shape=[1, 1], strides=[1, 1],
                                      pads=[0, 0, 0, 0]))
    print("%-42s %d -> %d channels" % (n.name[:42], cin, cout))
    done += 1

del g.node[:]
g.node.extend(new_nodes)
onnx.checker.check_model(m)
onnx.save(m, dst)
print("replaced %d channel pads" % done)

x = np.load("palm_calib_192.npy")[:6].astype(np.float32) / 255.0
a = ort.InferenceSession(src, providers=["CPUExecutionProvider"])
b = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
worst = 0.0
for i in range(len(x)):
    ra = a.run(None, {a.get_inputs()[0].name: x[i:i + 1]})
    rb = b.run(None, {b.get_inputs()[0].name: x[i:i + 1]})
    worst = max([worst] + [np.abs(p - q).max() for p, q in zip(ra, rb)])
print("outputs differ by at most %.3e over %d frames" % (worst, len(x)))
