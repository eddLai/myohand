# feat/direct-pipeline — 自己驅動 palm 與 landmark 模型

`mp.solutions.hands` 是一顆黑盒：丟一張圖進去、吐 21 個點出來，中間的縮圖、
偵測、解 anchor、NMS、旋轉裁切、追蹤全部在編譯好的 C++ 裡。這個分支把那一段
自己接起來，兩個 `.tflite` 檔一個位元都不改。

**動機是黑盒沒有入口的兩件事**：

1. **設執行緒數** —— MediaPipe 在四核零件上把 XNNPACK 釘死在 1 條，
   Python API 不給改。KD240 上這是 **7.8 FPS 對 19.0 FPS** 的差別。
2. **把 landmark 換成別的硬體跑** —— 模型是它自己載、自己餵的，插不進去。

## 成績 Measured

在 KD240（`ubuntu@120.126.83.228`，A53 四核）上，同一批錄影影格：

| 執行緒 | 整體平均 | 追蹤中的一幀 | palm | landmark |
|---|---|---|---|---|
| 1 | 7.4 FPS | 116.6 ms | 244.7 ms | 104.8 ms |
| 2 | 12.8 FPS | 67.9 ms | 134.7 ms | 58.4 ms |
| **4** | **19.0 FPS** | **46.0 ms** | 84.4 ms | 36.7 ms |
| MediaPipe 黑盒 | 7.8 FPS | 127.5 ms | — | — |

膠水（我們自己寫的部分）在 4 執行緒下佔 18%，其中 323 ms 是 `cv2.warpAffine`
的旋轉裁切（C 寫的）。真正屬於 numpy 的只有約 2.8% —— **A53 沒有 BLAS 這件事
沒有咬到我們。**

> ⚠️ vault 的 `A53_Inference_Baseline` 寫「4 執行緒後追蹤中 35.8 ms（27.9 FPS）」，
> 那是**只算模型**。加上膠水的真實數字是 46.0 ms（21.7 FPS）。
> 該文件的 36.2 ms landmark 數字則與本次量到的 36.7 ms 對到 1.4%。

## 正確性 Verified against MediaPipe, frame by frame

150 幀錄影（`nn/ref_capture.py` 錄的，含 MediaPipe 對同一批影像的答案）：

| | |
|---|---|
| 偵測 | MediaPipe 找到的 112 幀我全部也找到，只有 1 幀各自多／少 |
| 點位 | 中位 **1.3 px**（1280 寬） |
| 關節角度 | 中位 **1.2–2.2°** |
| **送給馬達的目標值** | 中位 **6–14 counts**（各軸行程 500–800） |
| `thumb_trust` 判定 | 111 幀只有 3 幀不同 |

**沒有追蹤的話點位差 14.6 px**，補上之後 1.3 px —— 追蹤那一段是整件事的關鍵，
而參考實作（`camera/blaze/`，來自 blaze_app_python）沒有提供，是這裡加的。

追蹤用的裁切規則跟偵測那條**不一樣**：旋轉基準取 wrist 到指根連線（不是 wrist
到中指根）、放大 2.0 倍（不是 2.6）、不做向下位移。照抄偵測那條會錯。

### 誤差集中在兩處，不是散開的

| 距離最近一次重偵測 | 幀數 | 最差角度中位 |
|---|---|---|
| 就是那一幀 | 4 | 23.4° |
| 之後 1–3 幀 | 10 | 14.7° |
| 之後 4–10 幀 | 21 | **3.0°** |
| 之後 11 幀以上 | 76 | **3.1°** |

兩邊各自偵測出的 ROI 差一點，約 4 幀收斂到一起。另有三幀點位只差 2.6–6.6 px
但對掌差 100° —— 那是拇指被遮住時模型本身不穩（見 `feat/thumb-decouple`），
不是管線的問題。

## 用法 Usage

```bash
python3 teleop/teleop_app.py --direct            # 4 執行緒
python3 teleop/teleop_app.py --direct --threads=2
python3 teleop/teleop_app.py                     # 原本的黑盒，預設
```

**預設行為完全沒變。** 這是唯一驅動過機器手的視覺路徑，中位吻合不等於逐幀吻合，
所以放在旗標後面。

## handedness 是量出來的，不是猜的

模型只給一個純量，teleop 要的是 `"Left"/"Right"` 加分數。對照 MediaPipe 在
111 幀上的標籤：

```
純量 ≥ 0.5  →  "Left"，  score = 純量        （|差| 中位 0.076）
純量 < 0.5  →  "Right"， score = 1 − 純量     （|差| 中位 0.003）
```

`thumb_trust` 會因為標籤錯就整幀拒絕，弄反的話拇指不是狂凍結就是完全不設防。

## 檔案 What is here

| | |
|---|---|
| `camera/hand_pipeline.py` | 管線 + `MediaPipeHands` 相容包裝 |
| `camera/blaze/` | 參考實作，Apache 2.0，含 LICENSE。常數來自 MediaPipe v0.10.9 的 pbtxt |
| `teleop/teleop_app.py` | +15 −3：`--direct` / `--threads` |
| `nn/ref_capture.py` | 錄「影像 + MediaPipe 的答案」 |
| `nn/refcap_audit.py` | 檢查錄影有沒有涵蓋會出問題的情況 |
| `nn/compare_tracked.py` | 逐幀比對角度 |
| `nn/compare_targets.py` | 比對送給馬達的六個目標值 |
| `nn/tail_diag.py` | 誤差集中在哪 |
| `nn/bench_pipeline.py` | 分段計時 + 執行緒掃描 |
| `nn/make_calib.py` | 產量化用的校正裁切 |
| `nn/handedness_map.py` | 量出 handedness 的對應關係 |
| **`nn/dpu/`** | **把 landmark 送上 DPU 的完整嘗試，見該資料夾的 README** |

## 還沒驗的 Not verified

- **實機手感**。數字吻合不代表體感一樣，特別是重偵測後那 4 幀。
- **`.112` 上感覺不到差別** —— 那台有 RTX 4080。2.4 倍是 KD240 上的事。
- **相機解析度不同時的行為**（量測都在 1280×720 與 320×180）。

## 合併前要處理的

`feat/filter-stage` 也在改 `teleop/teleop_app.py`（+326 −29）。試合併有
**一個衝突點**，在 argparse ——兩邊都在 `--max-frames` 附近加參數。
`hands = mp.solutions.hands.Hands(...)` 那行 filter-stage 沒動。
