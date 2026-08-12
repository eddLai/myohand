"""Put the name back on the padding that tf2onnx turned into numbers.

TensorFlow's SAME rule is a name: it says "pad however much this stride needs,
and put the odd one at the end". tf2onnx resolves it to explicit numbers, and
every downstream converter then has to emit a separate padding operator because
[0,0,1,1] is not something a symmetric-padding convolution can express. That
operator is what the DPU refuses, so the five stride-2 layers get cut out to the
CPU.

ONNX can state the rule directly through auto_pad, so the numbers are converted
back into SAME_UPPER wherever they agree with what that rule would have
produced. Layers whose padding does not match the rule are left alone - a
mismatch would mean the model does something SAME cannot describe, and silently
relabelling it would change the arithmetic.

    python3 restore_samepad.py in.onnx out.onnx
"""

import sys

import numpy as np
import onnx
from onnx import shape_inference

src, dst = sys.argv[1], sys.argv[2]
model = shape_inference.infer_shapes(onnx.load(src))
shapes = {v.name: [d.dim_value for d in v.type.tensor_type.shape.dim]
          for v in list(model.graph.value_info) + list(model.graph.input)}
weights = {t.name: t for t in model.graph.initializer}

changed, skipped = [], []
for node in model.graph.node:
    if node.op_type != "Conv":
        continue
    at = {a.name: a for a in node.attribute}
    if "pads" not in at:
        continue
    pads = list(at["pads"].ints)
    half = len(pads) // 2
    if pads[:half] == pads[half:]:
        continue                        # symmetric: nothing a converter must split

    strides = list(at["strides"].ints) if "strides" in at else [1] * half
    dil = list(at["dilations"].ints) if "dilations" in at else [1] * half
    w = weights.get(node.input[1])
    ksz = list(onnx.numpy_helper.to_array(w).shape[2:]) if w is not None else None
    inp = shapes.get(node.input[0])

    if not (ksz and inp and len(inp) == half + 2):
        skipped.append((node.name[-40:], pads, "缺形狀資訊"))
        continue

    # what SAME_UPPER would have produced for this layer
    want = []
    for i in range(half):
        size = inp[i + 2]
        eff = (ksz[i] - 1) * dil[i] + 1
        total = max(0, (-(-size // strides[i]) - 1) * strides[i] + eff - size)
        want += [total // 2, total - total // 2]
    begin, end = want[0::2], want[1::2]
    if pads != begin + end:
        skipped.append((node.name[-40:], pads, "與 SAME_UPPER 不符 %s" % (begin + end)))
        continue

    node.attribute.remove(at["pads"])
    node.attribute.append(onnx.helper.make_attribute("auto_pad", "SAME_UPPER"))
    changed.append((node.name[-40:], pads))

for n, p in changed:
    print("  改成 SAME_UPPER  %-42s %s" % (n, p))
for n, p, why in skipped:
    print("  ⚠ 保持原樣      %-42s %s  %s" % (n, p, why))

onnx.checker.check_model(model)
onnx.save(model, dst)
print("\n改了 %d 個、跳過 %d 個 -> %s" % (len(changed), len(skipped), dst))
