"""Ask the TensorFlow-side inspector the same question the PyTorch one answered.

The PyTorch route put 94 operators on the DPU and left 10 on the CPU, five of
them padding operators that only exist because Conv2d cannot express the uneven
padding TensorFlow's SAME rule produces. This model came straight from the
tflite file, where SAME survives as an attribute of the convolution and no
separate padding operator was ever created, so the comparison is between two
translations of one network rather than between two networks.

    python3 inspect_tf.py saved_model_dir DPUCZDX8G_ISA1_B4096 out_dir
"""

import collections
import os
import sys

import tensorflow as tf
from tensorflow_model_optimization.quantization.keras import vitis_inspect

sm, arch = sys.argv[1], sys.argv[2]
out = sys.argv[3] if len(sys.argv) > 3 else "inspect_tf_out"
os.makedirs(out, exist_ok=True)

try:
    model = tf.keras.models.load_model(sm, compile=False)
except Exception as e:
    # The converter wrote the graph with tf.saved_model.save, so it carries no
    # Keras metadata to rebuild layers from. Replaying the serving function on
    # a symbolic input recovers an equivalent functional model, which is all
    # the inspector needs.
    print("load_model failed (%s); tracing the serving signature instead"
          % type(e).__name__)
    fn = tf.saved_model.load(sm).signatures["serving_default"]
    spec = list(fn.structured_input_signature[1].values())[0]
    inp = tf.keras.Input(shape=spec.shape[1:], batch_size=1, dtype=spec.dtype)
    model = tf.keras.Model(inp, list(fn(inp).values()))
print("loaded %s -> %s" % (type(model).__name__, model.input_shape))

vitis_inspect.VitisInspector(target=arch).inspect_model(
    model, plot=True, plot_file=os.path.join(out, "model.svg"),
    dump_results=True, dump_results_file=os.path.join(out, "results.txt"),
    verbose=0)

# the dump names a device per layer; count them the same way as the torch side
path = os.path.join(out, "results.txt")
dev = collections.Counter()
notes = []
for line in open(path, encoding="utf-8", errors="replace"):
    s = line.strip()
    if s.lower().startswith("device:"):
        dev[s.split(":", 1)[1].strip()] += 1
    elif "notes:" in s.lower() and len(s) > 8:
        notes.append(s)

print("\n%s" % arch)
for k, v in dev.most_common():
    print("   %-6s %d" % (k, v))
if notes:
    print("\n落 CPU 的理由（前 12 條）")
    for n in notes[:12]:
        print("   " + n[:160])
print("\n報告：%s" % path)
