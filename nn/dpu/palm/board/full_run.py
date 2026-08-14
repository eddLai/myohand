"""Run the whole palm detector on the board and check it against the host.

There is no GraphRunner here, but the graph map showed the CPU segments are
two ReLUs and some scale conversions, so the chain is short enough to drive
by hand. Doing it by hand also makes the handover between segments explicit,
which is where a wrong layout would otherwise hide.

Tensors are passed between segments as int8 at their own fix point rather
than being converted to float and back. A ReLU is sign-preserving, so when
the two sides share a fix point it is exactly max(x, 0) on the raw bytes,
and the round trip through float would only add rounding of its own.

The comparison is against the quantiser's own simulation of the same model,
not against the float model: the DPU is supposed to reproduce the former
exactly, and any gap is a deployment bug rather than a quantisation cost.

    python full_run.py [n_frames]
"""
import os
import sys
import time

import numpy as np

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
XMODEL = "/tmp/palm_b1600.xmodel"
os.chdir("/home/ubuntu/Kria-PYNQ/DPU-PYNQ/pynq_dpu/kd240_notebooks")

from pynq_dpu import DpuOverlay

ov = DpuOverlay("dpu.bit")

import vart
import xir

g = xir.Graph.deserialize(XMODEL)
subs = g.get_root_subgraph().toposort_child_subgraph()
dev = [s.get_attr("device") if s.has_attr("device") else "?" for s in subs]

runners, buffers = {}, {}
for i, s in enumerate(subs):
    if dev[i] == "DPU":
        runners[i] = vart.Runner.create_runner(s, "run")


def fp(t):
    return t.get_attr("fix_point") if t.has_attr("fix_point") else None


# A subgraph's tensors come back as an unordered set here, so the four heads
# are picked out by shape rather than by position. They are the only tensors
# with these shapes, and the host flattens them in this order.
HEAD_SHAPES = [(12, 12, 108), (12, 12, 6), (24, 24, 36), (24, 24, 2)]


def run_frame(img):
    """img: uint8 (192,192,3) -> four float head tensors, NHWC."""
    store = {}
    it = runners[1].get_input_tensors()[0]
    x = img.astype(np.float32) / 255.0
    store[it.name] = np.clip(np.round(x * (2 ** fp(it))), -128, 127).astype(np.int8)

    for i, s in enumerate(subs):
        if dev[i] == "DPU":
            r = runners[i]
            ins = [np.ascontiguousarray(store[t.name].reshape([1] + list(t.dims)[1:]))
                   for t in r.get_input_tensors()]
            outs = [np.empty(tuple(t.dims), dtype=np.int8, order="C")
                    for t in r.get_output_tensors()]
            r.wait(r.execute_async(ins, outs))
            for t, o in zip(r.get_output_tensors(), outs):
                store[t.name] = o[0]
        elif dev[i] == "CPU":
            src = list(s.get_input_tensors())[0]
            dst = list(s.get_output_tensors())[0]
            v = store[src.name]
            if "relu" in {o.get_type() for o in s.get_ops()}:
                # the segment is fix2float, relu, float2fix, and the two fix
                # points differ, so the round trip is not optional: the output
                # is on a coarser scale than the input and doing max(x, 0) on
                # the raw bytes would hand the next segment the wrong units
                f = np.maximum(v.astype(np.float32) / (2 ** fp(src)), 0.0)
                store[dst.name] = np.clip(np.round(f * (2 ** fp(dst))),
                                          -128, 127).astype(np.int8)
            else:
                store[dst.name] = v.astype(np.float32) / (2 ** fp(src))

    by_shape = {}
    for name, arr in store.items():
        if arr.shape in [tuple(s) for s in HEAD_SHAPES] and arr.dtype == np.float32:
            by_shape[arr.shape] = arr
    missing = [s for s in HEAD_SHAPES if tuple(s) not in by_shape]
    if missing:
        raise SystemExit("no dequantised tensor with shape %s; have %s"
                         % (missing, sorted({a.shape for a in store.values()})))
    return [by_shape[tuple(s)] for s in HEAD_SHAPES]


calib = np.load("/tmp/palm_calib_192.npy")
ref = np.load("/tmp/qpalm_b1600_int8.npy")     # host simulation of the same model
print("frames", calib.shape, "reference", ref.shape)

# host layout is NCHW and flattened in this order
SHAPES = [(108, 12, 12), (6, 12, 12), (36, 24, 24), (2, 24, 24)]
SIZES = [int(np.prod(s)) for s in SHAPES]

rows = np.empty((N, sum(SIZES)), dtype=np.float32)
t0, worst, rel = time.time(), 0.0, []
for i in range(N):
    heads = run_frame(calib[i])
    off = 0
    for h, sh, n in zip(heads, SHAPES, SIZES):
        want = ref[i][off:off + n].reshape(sh)
        got = np.asarray(h).transpose(2, 0, 1)          # NHWC -> CHW
        rows[i, off:off + n] = got.ravel()
        off += n
        d = np.abs(got - want).max()
        worst = max(worst, float(d))
        rng = float(want.max() - want.min())
        rel.append(d / rng if rng else 0.0)
el = time.time() - t0

np.save("/tmp/board_out.npy", rows)
print("ran %d frames in %.2f s = %.1f fps (whole detector, DPU + CPU glue)"
      % (N, el, N / el))
print("largest disagreement with the host simulation: %.4f absolute, "
      "%.2f%% of range" % (worst, 100 * max(rel)))
print("median relative disagreement %.3f%%" % (100 * float(np.median(rel))))
print("saved /tmp/board_out.npy", rows.shape,
      "-- decode it against the float model on the host")
