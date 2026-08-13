"""Turn onnx2torch PReLU calls into real nn.PReLU modules.

Reshaping the slopes moved onnx2torch onto its native-prelu path, which is
what the DPU wants, but left the slope as a constant in the traced graph
rather than a registered parameter, and vai_q_pytorch looks it up in
state_dict by name and raises KeyError instead.

nn.PReLU owns its weight, so making the substitution at the module level
answers both: the quantiser finds the parameter where it expects it, and the
compiler sees one operator instead of a clone, a comparison and a masked
index.

Import this and call swap(model) after onnx2torch.convert.
"""
import torch
from torch import nn


def swap(model):
    """Replace every OnnxPReLU call with nn.PReLU; returns how many."""
    g = model.graph
    mods = dict(model.named_modules())
    done = 0
    for node in list(g.nodes):
        if node.op != "call_module":
            continue
        target = mods.get(node.target)
        if type(target).__name__ != "OnnxPReLU":
            continue
        if len(node.args) != 2:
            raise SystemExit("unexpected PReLU signature: %s" % (node.args,))
        slope_node = node.args[1]
        if slope_node.op != "get_attr":
            raise SystemExit("slope is not a constant: %s" % slope_node.op)
        # the target is a dotted path such as initializers.onnx_initializer_0,
        # which plain getattr does not walk
        obj = model
        for part in slope_node.target.split("."):
            obj = getattr(obj, part)
        w = obj.detach().reshape(-1).clone()
        new = nn.PReLU(num_parameters=w.numel())
        with torch.no_grad():
            new.weight.copy_(w)
        # the module tree is flat here, and setattr on a dotted name fails
        parent, _, leaf = node.target.rpartition(".")
        setattr(mods[parent] if parent else model, leaf, new)
        node.args = (node.args[0],)
        done += 1
    g.lint()
    model.recompile()
    return done
