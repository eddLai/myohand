"""Cut the palm graph at the four head convolutions.

The quantiser traces the graph into PyTorch, and the tail defeats it: the
reshapes that fold 24x24x2 anchors into 1152 rows carry their shape as a
computed tensor, which the tracer resolves to [1,1,1] and then refuses. The
tail is also the part that does no arithmetic - a transpose, a reshape and a
concatenate per head - so it is the part with the least to lose.

Cutting there leaves the convolutions, which is what the DPU would run in any
case; the pipeline already owns anchor decoding, and now owns three more
lines of layout.

    python cut_palm_tail.py <in.onnx> <out.onnx>
"""
import sys

import onnx

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
heads = [n for n in m.graph.node
         if n.op_type == "Conv" and ("regressor_palm" in n.name or "classifier_palm" in n.name)]
if not heads:
    raise SystemExit("no head convolutions found")
outs = [n.output[0] for n in heads]
for n, o in zip(heads, outs):
    print("head", n.name[:56], "->", o[:56])

onnx.utils.extract_model(src, dst, [m.graph.input[0].name], outs)
k = onnx.load(dst)
print("cut model outputs:")
for o in k.graph.output:
    print("  ", o.name[:60], [d.dim_value for d in o.type.tensor_type.shape.dim])
print("nodes:", len(k.graph.node))
