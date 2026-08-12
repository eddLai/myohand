# nn/dpu — 把 hand_landmark 送上 DPU 的嘗試（**結論：PTQ 走不通**）

KD240 的 PL 上可以放一顆 DPU（Xilinx 的神經網路加速器）。把 `hand_landmark_lite`
放上去，追蹤中的一幀可以從 46 ms 降到約 13 ms（約 75 FPS）。這個資料夾是
那件事的完整嘗試。

**結論：模型編得出 `.xmodel`、94/105 個 op 落在 DPU 上，但 8 bit 量化之後
讀數不能用。三種量化方式全試過，最好的一種仍差 93–135 counts。**

跑這些腳本需要 Vitis-AI 3.5 環境（`ntk@120.126.83.20` 的 `/media/ntk/sda4/Vitis-AI`），
不在本 repo 的相依裡。

## 為什麼會失敗 The one number that explains it

模型的主幹是 47 個卷積，末端分出四顆頭，共用同一個 672 維特徵向量：

```
224x224 裁切圖 → 主幹 47 conv → 672 維特徵 ─┬→ Gemm → 影像座標 (63)   值域 0-224
                                            ├→ Gemm → world 座標 (63)  值域 0-0.08 公尺
                                            ├→ Gemm → presence  (1)
                                            └→ Gemm → handedness (1)
```

量化後 672 維特徵本身有 **2.9% 的相對偏差**（中位；p95 7.6%）。同一份雜訊
打進兩顆頭：

| 輸出 | 值域 | 量化後偏差 | 後果 |
|---|---|---|---|
| 影像座標 | 224 | 1.6 px | ✅ 看不出來 |
| **world 座標** | **0.084 公尺** | **0.026 公尺** | ❌ 指骨才 2–3 公分 |

**輸出尺度差 2000 倍，所以一個沒事、一個死。** 而 `camera/hand_mapping.py`
只吃 world —— 它不能用影像座標，因為投影距離會隨視角改變（見該檔開頭）。

## 三種量化，換算成角度 Measured, in the units the hand moves in

對浮點模型的偏差中位數：

| 做法 | curl_lo | curl_hi | thumb | opp |
|---|---|---|---|---|
| Vitis AI（2 的冪次尺度） | 34.4° | 101.0° | 77.8° | 128.2° |
| 標準 int8（TFLite 逐通道） | 14.3° | 46.2° | 20.1° | 48.7° |
| 主幹量化 + world 頭留浮點 | 4.1° | 10.8° | 9.0° | 15.7° |

校正窗總共只涵蓋 816 counts，而 15.7° = 135 counts。**最好的一種也不能用。**

Vitis 的 2 的冪次尺度確實更糟（DPU 用位移而非乘法做縮放），但**它不是根因** ——
標準 int8 也一樣壞。

## 轉檔路線 The route that works, mechanically

Vitis-AI 3.5 不吃 TFLite，社群的做法是繞 PyTorch（zmurez/MediaPipePyTorch），
但那個 port 只有 v0.07 的模型，我們用的是 **v0.10.21**。這裡走的是另一條：

```
hand_landmark_lite.tflite (0.10.21)
  ↓ tf2onnx --opset 11          opset 13 的 Squeeze 把軸當輸入傳，量化器不吃
  ↓ onnxsim
  ↓ onnx2torch
  ↓ vai_q_pytorch               inspect → calib → test → export_xmodel
  ↓ vai_c_xir
.xmodel
```

**這條路沒有人發表過**，它通 —— 所以「0.10 的模型上不了 Vitis-AI」這個社群
共識是可以繞開的，卡住的是精度不是轉檔。

## 落在 CPU 的 10 個 op

```
5  pad-fix        ⚠️ 在網路中段，每個都是一次 DPU→CPU→DPU
2  Sigmoid        最尾端，2 個純量
1  aten::mean     GlobalAvgPool，onnx2torch 對映錯
1  reshape        被上一個連累
1  transpose      輸入邊界
```

那 5 刀來自 **TensorFlow 的 SAME padding 在 stride 2 時是不對稱的**
（`[0,0,1,1]` / `[1,1,2,2]`）。ONNX 的 Conv 吃得下不對稱 padding，
PyTorch 的 `Conv2d` 吃不下，所以轉檔時被拆成「先補、再卷積」——
而 DPU 只支援 SYMMETRIC 的 pad，不支援獨立的 CONSTANT pad。

每幀的代價：1.4 MB 出 + 1.4 MB 回，外加 5 次停等。

**乾淨的圖做得出來**（`tflite2tensorflow` 直轉的凍結圖有 47 個 SAME 卷積、
零個 Pad、輸出位元相同），但三條把它餵進量化器的路都被工具擋住：

| 路線 | 卡點 |
|---|---|
| TF2 / Keras | `vai_q_tensorflow2` 只認真正的 Keras 層；`onnx2tf` 產出 194 個 `TFOpLambda` 一層不收 |
| ONNX 直接（`vai_q_onnx`） | wheel 網址 404，且產出是量化 ONNX 不是 xmodel |
| PyTorch + SAME_UPPER | `onnx2torch`：`"SAME_UPPER" auto_pad is not implemented` |

## 檔案 What is here

| 檔案 | 用途 |
|---|---|
| `opscan.py` | 掃 tflite 的 op 種類（找出 palm_detection 有 26 個 PReLU） |
| `prelu_probe.py` | 建兩個小網路，證明 PReLU 會把圖切成兩個 DPU subgraph |
| `onnx_route/inspect_dpu.py` | vai_q_pytorch 的 Inspector：每個 op 落 DPU 還是 CPU |
| `onnx_route/restore_samepad.py` | 把明確 pad 改回 `auto_pad=SAME_UPPER`（5 個全部吻合） |
| `onnx_route/to_savedmodel.py` | ONNX → TF SavedModel（`onnx2tf`，含兩個 bug 的繞法） |
| `onnx_route/roundtrip_check.py` | 轉檔前後逐輸出比對，**不驗就不要往下做** |
| `onnx_route/quantize_xmodel.py` | 校正 + 匯出 `.xmodel` |
| `onnx_route/eval_quant.py` | 一般 PTQ vs `fast_finetune`，換算成角度 |
| `onnx_route/eval_ft.py` | 繞過 `load_ft_param` 的 `None` bug |
| `onnx_route/split_head_test.py` | **關鍵實驗**：主幹量化 + world 頭留浮點 |
| `int8_sanity.py` / `int8_angles.py` | 標準 int8 對照，分辨「模型不能量化」還是「這個量化器不行」 |
| `tf_route/inspect_tf.py` | TF2 側的 Inspector（被 `TFOpLambda` 擋住） |

校正資料由 `nn/make_calib.py` 產出（118 張真實的 224×224 旋轉裁切，
從 `hand_pipeline` 的追蹤迴圈直接接出來）—— 這是管線做出來之後才有辦法產的，
不能用原始相機畫面代替。

## 還沒被排除的救法 What is left

**量化感知訓練（QAT），用蒸餾。** 老師 = 現在的浮點模型，學生 = 量化版，
資料 = 任何手的照片。**不需要 Google 沒公開的標註資料**，老師會給答案。
那是一個獨立的訓練專案。

## 一個可能反轉分配的假設 An untested hypothesis

目前談定的分配是「landmark 進 PL、palm_detection 留 PS」。有兩個理由指向相反：

| | landmark | palm_detection |
|---|---|---|
| 輸出 | 公制骨架，8 cm 尺度要 mm 精度 | 框 + 7 個點，只拿來決定裁哪一塊 |
| 對量化的耐受度 | ❌ 已實測，死 | 🔬 應該高很多 |
| 單次耗時（A53 4 執行緒） | 36.7 ms | **84.4 ms** |
| 多久跑一次 | 每一幀 | 只在追丟時 |

支持的證據：兩邊 ROI 差 11 px 時，角度只差 2.7–5.9° —— 裁切位置的誤差會被吸收。
**這還沒實測。**

## 硬體上限 K24 cannot host what KV260 hosts

K24（XCK24）全部家當：70,560 LUT / 141,120 FF / 360 DSP48E2 / 216 BRAM36 / 0 URAM。

| DPU | LUT | DSP | BRAM | 塞得下？ |
|---|---|---|---|---|
| B4096（**KV260 用的**） | 52,161 | **710** | 255 | ❌ |
| B2304 | 42,127 | **438** | 165 | ❌ |
| B1024 | 34,074 | 230 | 104 | ✅ |
| B512 | 26,922 | 118 | 72 | ✅ |

而且容器裡的 `arch.json` 只有 KV260 / ZCU102 / ZCU104 —— **KD240 沒有官方
DPU overlay，要自己走 DPU-TRD**。做 PL 的人若照 KV260 的配置規劃會做不出來。

## 產物在哪 Artefacts (not committed)

二進位檔留在 `ntk@120.126.83.20:/media/ntk/sda4/yuechi/palmdet_vitis/`：

```
onnx_route/hl11_sim.onnx              量化器吃得下的 ONNX
onnx_route/hl11_sim_B4096/            Inspector 報告（94 dpu / 10 cpu）
onnx_route/q_B4096/GraphModule_int.xmodel   編出來的 xmodel（精度不可用）
onnx_route/calib_crops.npy            118 張校正裁切
tf_route/saved_model/model_float32.pb 零 Pad、47 個 SAME 卷積、位元相同
```

最後那個是最值錢的 —— 哪天要把 5 刀降到 0，從它開始，不必重走一遍。
