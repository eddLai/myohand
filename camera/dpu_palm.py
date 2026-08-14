"""Run the palm detector's convolutions on the DPU, keep everything else.

The pipeline reaches the palm model through BlazeDetector.predict_core, which
takes a letterboxed image and returns the two head tensors. Everything after
that -- anchor decode, weighted NMS, the palm-to-crop geometry -- is the
reference implementation and is what the deployed pipeline was validated
against, so replacing only predict_core keeps the change to the one thing
that actually moved to hardware.

The compiled graph is three DPU segments with two ReLUs between them. There
is no GraphRunner in these bindings, so the segments are driven in order and
the ReLUs done in numpy; the fix points differ across each ReLU, so the round
trip through float is not optional.

    from dpu_palm import DpuPalm
    DpuPalm().attach(pipeline.det)
"""
import os
import sys

import numpy as np

# The runtime bindings sit outside whichever environment teleop was started
# in, and the two cannot be merged: pynq wants a pycparser older than the one
# mediapipe's environment carries. They are appended, never inserted, so the
# calling environment's numpy and cv2 keep winning.
for _extra in ("/usr/lib/python3/dist-packages",                      # vart, xir
               "/usr/local/share/pynq-venv/lib/python3.10/site-packages",
               os.path.expanduser("~/Kria-PYNQ/DPU-PYNQ")):           # pynq_dpu
    if os.path.isdir(_extra) and _extra not in sys.path:
        sys.path.append(_extra)

XMODEL = os.path.expanduser("~/rh56f1_kd240/models/palm_b1600.xmodel")
BITDIR = os.path.expanduser("~/Kria-PYNQ/DPU-PYNQ/pynq_dpu/kd240_notebooks")

# The int8 detector's scores sit at roughly 0.6 of the float model's, so the
# default 0.5 throws away a quarter of the hands it found. Measured on the
# 150-frame reference recording: 0.40 recovers 85 of 117 with no detection on
# any of the 33 frames the float model calls empty, and 0.30 reaches 94 at the
# cost of 2. 0.35 sits between them and is where this starts.
SCORE_THRESH = 0.35

HEAD_SHAPES = [(12, 12, 108), (12, 12, 6), (24, 24, 36), (24, 24, 2)]


def _fp(t):
    return t.get_attr("fix_point") if t.has_attr("fix_point") else None


class DpuPalm:
    def __init__(self, xmodel=XMODEL, overlay="dpu.bit", load_overlay=True):
        # The bitstream stays in the PL after the process that downloaded it
        # exits, and driving the DPU afterwards needs only vart and xir, which
        # are plain extension modules. Loading the overlay, by contrast, drags
        # in all of pynq -- including a microblaze RPC layer that wants a
        # pycparser older than the one mediapipe's environment carries. So the
        # download is attempted and allowed to fail: if the DPU is already
        # there, create_runner will succeed anyway, and if it is not, that call
        # is where the failure belongs.
        self.ov = None
        if load_overlay:
            try:
                import asyncio
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())
                here = os.getcwd()
                os.chdir(BITDIR)              # DpuOverlay resolves relative
                try:
                    from pynq_dpu import DpuOverlay
                    self.ov = DpuOverlay(overlay)
                finally:
                    os.chdir(here)
            except Exception as e:
                print("overlay not loaded here (%s: %s); assuming the DPU is "
                      "already in the PL" % (type(e).__name__, e))

        import vart
        import xir

        g = xir.Graph.deserialize(xmodel)
        self.subs = g.get_root_subgraph().toposort_child_subgraph()
        self.dev = [s.get_attr("device") if s.has_attr("device") else "?"
                    for s in self.subs]
        self.runners = {i: vart.Runner.create_runner(s, "run")
                        for i, s in enumerate(self.subs) if self.dev[i] == "DPU"}
        it = self.runners[min(self.runners)].get_input_tensors()[0]
        self.in_name, self.in_scale = it.name, 2 ** _fp(it)

    def heads(self, x):
        """x: (1,192,192,3), raw 0-255 pixels -> four float heads, NHWC.

        The reference divides by 255 inside predict_core rather than before
        it, so a replacement for predict_core has to do the same; feeding the
        raw range straight to the fix point saturates every activation and
        the detector then finds nothing at all.
        """
        store = {self.in_name: np.clip(np.round(x[0] / 255.0 * self.in_scale),
                                       -128, 127).astype(np.int8)}
        for i, s in enumerate(self.subs):
            if self.dev[i] == "DPU":
                r = self.runners[i]
                ins = [np.ascontiguousarray(
                    store[t.name].reshape([1] + list(t.dims)[1:]))
                    for t in r.get_input_tensors()]
                outs = [np.empty(tuple(t.dims), dtype=np.int8, order="C")
                        for t in r.get_output_tensors()]
                r.wait(r.execute_async(ins, outs))
                for t, o in zip(r.get_output_tensors(), outs):
                    store[t.name] = o[0]
            elif self.dev[i] == "CPU":
                src = list(s.get_input_tensors())[0]
                dst = list(s.get_output_tensors())[0]
                v = store[src.name]
                if "relu" in {o.get_type() for o in s.get_ops()}:
                    f = np.maximum(v.astype(np.float32) / (2 ** _fp(src)), 0.0)
                    store[dst.name] = np.clip(np.round(f * (2 ** _fp(dst))),
                                              -128, 127).astype(np.int8)
                else:
                    store[dst.name] = v.astype(np.float32) / (2 ** _fp(src))

        by_shape = {a.shape: a for a in store.values()
                    if a.dtype == np.float32 and a.shape in
                    [tuple(s) for s in HEAD_SHAPES]}
        return [by_shape[tuple(s)] for s in HEAD_SHAPES]

    def predict_core(self, x):
        """The two tensors the reference decoder expects, 8x8 level first."""
        r16, c16, r8, c8 = self.heads(x)
        rows = lambda t, k: t.reshape(1, -1, k)          # already NHWC
        s8, r8_ = rows(c8, 1), rows(r8, 18)
        s16, r16_ = rows(c16, 1), rows(r16, 18)
        return (np.concatenate([s8, s16], axis=1),
                np.concatenate([r8_, r16_], axis=1))

    def attach(self, det):
        det.predict_core = self.predict_core
        det.min_score_thresh = SCORE_THRESH
        return det
