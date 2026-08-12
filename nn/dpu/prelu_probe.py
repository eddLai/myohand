"""Will a DPU run PReLU, or does every one of them split the graph?

palm_detection carries 26 PReLU activations. The DPU has a fixed menu of
activations in silicon, and anything off it goes back to the CPU: 26 handoffs
in a 233 node graph would cost more in DDR round trips than the accelerator
saves, so this single question decides whether the model is worth porting at
all.

Asking the documentation invites a stale answer, so this asks the compiler.
Two networks, identical apart from the activation, are quantized and compiled
for the same DPU the KV260 runs. LeakyReLU is the control: it is documented as
supported, so if it also splits then the harness is wrong rather than PReLU.
What matters in the output is the number of subgraphs and which device each
one is assigned to.
"""

import os
import sys

import torch
import torch.nn as nn
from pytorch_nndct.apis import torch_quantizer

OUT = "/work/probe"


class Net(nn.Module):
    def __init__(self, act):
        super(Net, self).__init__()
        self.c1 = nn.Conv2d(3, 16, 3, padding=1, bias=True)
        self.act = act
        self.c2 = nn.Conv2d(16, 16, 3, padding=1, bias=True)

    def forward(self, x):
        return self.c2(self.act(self.c1(x)))


def build(name, act):
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    model = Net(act).eval()
    dummy = torch.randn(1, 3, 64, 64)
    # calibration pass: the quantizer needs to see activations before it can
    # pick scales, and one batch of noise is enough for a graph this shape
    q = torch_quantizer("calib", model, (dummy,), output_dir=d, device=torch.device("cpu"))
    q.quant_model(dummy)
    q.export_quant_config()
    q = torch_quantizer("test", model, (dummy,), output_dir=d, device=torch.device("cpu"))
    q.quant_model(dummy)
    q.export_xmodel(output_dir=d, deploy_check=False)
    return d


for name, act in (("prelu", nn.PReLU(16)),
                  ("leaky", nn.LeakyReLU(0.1))):
    print("\n" + "=" * 60)
    print("building %s" % name)
    print("=" * 60)
    try:
        print("-> %s" % build(name, act))
    except Exception as e:            # noqa: BLE001 - the failure is the result
        print("FAILED: %s: %s" % (type(e).__name__, e))
