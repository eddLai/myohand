# nn/dpu/palm — palm_detection onto the DPU

`../` holds the landmark attempt, which quantises badly and stayed on the
A53. This holds the detector, which does not, and the order below is the
path that got it onto the KD240's DPU.

Run from a Vitis AI container with the model directory mounted. Nothing here
touches a weight: the two rewrites are exact identities, and the accuracy
cost is entirely the 8-bit arithmetic.

## The order

| | script | what it does |
|---|---|---|
| 1 | `cut_palm_tail.py` | extract the graph at the four head convs, so the dynamic reshape at the tail stops blocking the quantiser |
| 2 | `expand_prelu.py` | `PReLU(x) = ReLU(x) − a·ReLU(−x)`, written as ReLU + two 1×1 depthwise convs + Add. **This is what unblocks the partition** |
| 3 | `expand_pad.py` | zero-padding C channels as a 1×1 Conv with identity rows and zero rows; the DPU refuses CONSTANT-mode Pad |
| 4 | `quant_palm.py` | calibrate and export. Takes the target as an argument -- the fingerprint of the DPU actually in the PL, not a name |
| 5 | `roi_delta.py` | what quantisation costs the crop, in pixels of the frame it is cut from |
| 6 | `score_probe.py` | whether the lost detections shifted or scrambled |
| 7 | `fp_probe.py` | what a lower threshold costs in false detections |

`fix_prelu_shape.py` and `swap_prelu.py` are kept as evidence rather than as
steps: both cleaned the representation (637 → 377 ops, 52 transposes to none)
and **neither changed the partition**, which is what established that the
problem was the operator and not the graph's tidiness.

`augment_calib.py` built a 450-frame calibration set from the recorded 150.
It made the detection rate worse at every threshold. Kept for the same
reason.

## What came out

- DPU subgraphs 33 → 6 → 3, no weights touched, no retraining
- crop centre within 0.01 px of float at the median, 0.28 px at worst
- int8 scores at 0.6 of float, so the threshold moves from 0.50 to 0.35–0.40:
  85 of 117 detections against 73, and nothing on the 33 frames the float
  model calls empty

## vivado/

The block design that builds a B1024 into the PL from nothing but the IP:
`bd_b1024.tcl` is the whole flow through to a bitstream, `set_b1024.tcl` and
`probe_ip.tcl` read back what the IP derived, `synth_b1024.tcl` synthesises
the DPU alone to answer whether the part holds it. `impl_util.rpt` and
`impl_timing.rpt` are the results on xck24 at 200/400 MHz: 56.6% LUT, 63.9%
DSP, WNS +0.500 ns.

That bitstream is not what the board runs. The DPU-PYNQ overlay already on
the KD240 is a B1600 -- larger, and already working -- so the compiled model
targets its fingerprint instead. The tcl is here so the PL is ours to
rebuild when the design needs to hold more than a DPU.
