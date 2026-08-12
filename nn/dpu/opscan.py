"""Which operators does palm detection actually use, and can a DPU run them?

The compiler will answer this eventually, but not before someone has built a
DPU design for a board that has none. The operator list answers most of it
today: DPUCZDX8G runs a fixed menu, and anything off the menu is handed back
to the CPU. One unsupported operator in the middle of a graph splits it, and a
graph that ping-pongs between PL and DDR is slower than leaving it on the CPU.

Support here is DPUCZDX8G's documented set, with the conditions that actually
bite folded in: convolutions are limited in kernel and stride, pooling is
limited in window, and only a few activations exist in hardware. Anything this
script is unsure about is reported as unsure rather than quietly passed, since
a false green here costs weeks.
"""

import collections
import sys

from ai_edge_litert.interpreter import Interpreter

# DPUCZDX8G, per PG338 / the Vitis AI user guide op-support tables
SUPPORTED = {
    "CONV_2D", "DEPTHWISE_CONV_2D", "TRANSPOSE_CONV", "FULLY_CONNECTED",
    "MAX_POOL_2D", "AVERAGE_POOL_2D", "MEAN", "ADD", "MUL", "CONCATENATION",
    "RELU", "RELU6", "LEAKY_RELU", "PAD", "RESHAPE", "LOGISTIC", "HARD_SWISH",
    "RESIZE_NEAREST_NEIGHBOR", "RESIZE_BILINEAR", "SPLIT", "STRIDED_SLICE",
}
# runs on the DPU only under conditions the op list cannot show
CONDITIONAL = {
    "STRIDED_SLICE": "只有特定切法",
    "RESHAPE": "只有不改變記憶體佈局的形狀",
    "MEAN": "只有當它其實是 global average pooling",
    "PAD": "只有零填充且落在卷積前",
    "RESIZE_BILINEAR": "align_corners 有限制",
}
NEVER = {
    "CUSTOM": "自訂 op，DPU 不可能有",
    "TFLITE_DETECTION_POSTPROCESS": "anchor 解碼與 NMS，本來就該留 CPU",
    "SOFTMAX": "通常放最後，切出來留 CPU 影響小",
    "TRANSPOSE": "會打亂佈局",
    "GATHER": "動態索引",
}

for path in sys.argv[1:]:
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    print("\n=== %s ===" % path.split("/")[-1])
    for tag, dets in (("輸入", it.get_input_details()),
                      ("輸出", it.get_output_details())):
        for d in dets:
            print("  %s  %-22s %-18s %s" % (tag, d["name"], d["shape"],
                                            d["dtype"].__name__))

    ops = collections.Counter(o["op_name"] for o in it._get_ops_details())
    print("  共 %d 個節點，%d 種 op" % (sum(ops.values()), len(ops)))
    ok = unsure = bad = 0
    for name, n in ops.most_common():
        if name in NEVER:
            verdict, note, bad = "✗ 不支援", NEVER[name], bad + n
        elif name in CONDITIONAL:
            verdict, note, unsure = "? 有條件", CONDITIONAL[name], unsure + n
        elif name in SUPPORTED:
            verdict, note, ok = "✓ 支援", "", ok + n
        else:
            verdict, note, unsure = "? 未知", "不在我的清單上，要查 PG338", unsure + n
        print("    %-28s x%-4d %-10s %s" % (name, n, verdict, note))
    total = ok + unsure + bad
    print("  → 支援 %d / 有條件或未知 %d / 不支援 %d  （共 %d）"
          % (ok, unsure, bad, total))
    if bad == 0 and unsure == 0:
        print("  → 全部落在 DPU 的清單內，理論上編成一個 subgraph")
    elif bad == 0:
        print("  → 沒有硬性不支援的，但有 %d 個節點要實際編過才知道" % unsure)
    else:
        print("  → 有 %d 個節點 DPU 不可能跑，圖會被切開" % bad)
