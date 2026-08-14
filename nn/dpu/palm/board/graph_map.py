"""What sits between the DPU passes, and whether it is worth writing by hand.

There is no GraphRunner in these python bindings, so the CPU segments have to
be executed by us. Whether that is ten lines of numpy or a reimplementation
of four convolutions depends entirely on what ops the compiler left behind,
so the first move is to read them rather than guess.

    python graph_map.py <xmodel>
"""
import sys
from collections import Counter

import xir

g = xir.Graph.deserialize(sys.argv[1] if len(sys.argv) > 1 else "/tmp/palm_b1600.xmodel")
subs = g.get_root_subgraph().toposort_child_subgraph()
print("%d device subgraphs\n" % len(subs))

for i, s in enumerate(subs):
    dev = s.get_attr("device") if s.has_attr("device") else "?"
    ops = Counter(o.get_type() for o in s.get_ops())
    ins = [t.name for t in s.get_input_tensors()]
    outs = [t.name for t in s.get_output_tensors()]
    print("[%2d] %-4s %s" % (i, dev, dict(ops)))
    for t in s.get_input_tensors():
        print("       in  %-56s %s" % (t.name[-56:], t.dims))
    for t in s.get_output_tensors():
        print("       out %-56s %s" % (t.name[-56:], t.dims))
