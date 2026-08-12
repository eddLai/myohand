"""Score the finetuned quantisation, loading its parameters the long way round.

fast_finetune completed all 51 layers and wrote param.pth, quant_info.json and
bias_corr.pth, then the quantiser's own loader raised on a bias correction entry
that came back as None. The finetuning itself is what took the time and it is on
disk, so the parameters are loaded directly into the test model instead, which
skips only the bias correction step the loader was in the middle of.

Angles are compared against the float model on the same crops, so plain
calibration and finetuning are scored on identical ground.

    python3 eval_ft.py model.onnx calib.npy arch q_ft
"""

import os
import sys

import numpy as np
import torch
from onnx2torch import convert
from pytorch_nndct.apis import torch_quantizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hand_mapping as hm

onnx_path = sys.argv[1] if len(sys.argv) > 1 else "hl11_sim.onnx"
calib_path = sys.argv[2] if len(sys.argv) > 2 else "calib_crops.npy"
arch = sys.argv[3] if len(sys.argv) > 3 else "DPUCZDX8G_ISA1_B4096"
KEYS = ("curl_lo", "curl_hi", "thumb", "opp")

crops = np.load(calib_path)
model = convert(onnx_path).eval()
N = min(48, len(crops))


def pts(a):
    return [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
            for x, y, z in a]


def measure(m):
    ang, img = [], []
    with torch.no_grad():
        for c in crops[:N]:
            outs = [np.asarray(o.detach()) for o in m(torch.from_numpy(c[None]))]
            sixty3 = [o for o in outs if o.size == 63]
            img.append(sixty3[0].reshape(21, 3))          # pixels in the crop
            ang.append(hm.raw_features(pts(sixty3[-1].reshape(21, 3))))
    return ang, np.stack(img)


base_ang, base_img = measure(model)


def score(name, out_dir, use_ft):
    q = torch_quantizer("test", model, (torch.from_numpy(crops[:1]),),
                        device=torch.device("cpu"), target=arch,
                        output_dir=out_dir)
    if use_ft:
        sd = torch.load(os.path.join(out_dir, "param.pth"),
                        map_location="cpu")
        missing, unexpected = q.quant_model.load_state_dict(sd, strict=False)
        print("%s：載入 %d 個張量（缺 %d、多 %d）"
              % (name, len(sd), len(missing), len(unexpected)))
    ang, img = measure(q.quant_model)
    px = np.abs(img[:, :, :2] - base_img[:, :, :2]).max(axis=(1, 2))
    print("\n%s   影像座標偏差 中位 %.1f px（裁切圖 224 寬）"
          % (name, np.median(px)))
    for k in KEYS:
        v = np.array([abs(a[k] - b[k]) for a, b in zip(base_ang, ang)])
        v = np.minimum(v % 360.0, 360.0 - v % 360.0)
        print("   %-9s 中位 %7.2f°  p95 %7.2f°" % (k, np.median(v),
                                                  np.percentile(v, 95)))
    return q


score("一般 PTQ", "q_plain", False)
q = score("fast_finetune", "q_ft", True)
try:
    q.export_xmodel(output_dir="q_ft", deploy_check=False)
    print("\nq_ft/ 的 xmodel 已輸出")
except Exception as e:
    print("\nxmodel 輸出失敗：%s" % e)
