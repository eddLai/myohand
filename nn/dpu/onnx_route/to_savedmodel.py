"""Convert the ONNX back to a SavedModel, telling the converter the layout.

onnx2tf assumes an ONNX graph came from PyTorch and is therefore NCHW, so it
transposes on the way in. This graph came from a tflite file and is already
NHWC, and without being told so the converter reads 224x224x3 as C=224, H=224,
W=3 and produces a model whose input no longer accepts an image. The flag that
prevents this also takes a code path that loads a bundled sample image, which
fails under the numpy in this container because the file is pickled - so np.load
is relaxed for the duration.

    python3 to_savedmodel.py model.onnx out_dir input_name
"""

import sys

import numpy as np

# The sample images it wants to fetch are only fed through both graphs to score
# candidate transpositions against each other, so any input of the right shape
# scores them equally well - and this container cannot reach the download host.
import onnx2tf.onnx2tf as _o2t
import onnx2tf.utils.common_functions as cf

_fake = lambda: np.random.default_rng(0).random((20, 128, 128, 3),
                                                dtype=np.float32)
cf.download_test_image_data = _fake
_o2t.download_test_image_data = _fake   # imported by name, so patch it there too

from onnx2tf import convert

onnx_path, out_dir = sys.argv[1], sys.argv[2]
name = sys.argv[3] if len(sys.argv) > 3 else "input_1"

convert(input_onnx_file_path=onnx_path, output_folder_path=out_dir,
        keep_nwc_or_nhwc_or_ndhwc_input_names=[name],
        output_signaturedefs=True, copy_onnx_input_output_names_to_tflite=True,
        output_h5=True,          # vai_q_tensorflow2 quantizes Keras models only
        non_verbose=True)
print("wrote", out_dir)
