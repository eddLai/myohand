# kd240-dpu-20260814 — 把 palm_detection 放進 KD240 的 PL

`feat/direct-pipeline` 把兩顆模型從黑盒裡拆出來自己驅動，為的是能設執行緒數、
能把模型換到別的硬體上跑。**這個分支做的就是那個「別的硬體」**：palm 偵測器的
卷積跑在 DPU 上，解碼、landmark、追蹤留在 A53。

## 為什麼是 palm，不是 landmark

前一輪（`nn/dpu/`，2026-08-12）試的是 landmark，結論是**不能用**：四顆輸出頭
共用一個 672 維特徵向量，量化後帶 2.9% 雜訊，影像座標吸收成 1.6 px 沒差，
但 world 骨架吸收成 2.6 公分——而指骨才 2–3 公分，`hand_mapping` 又只吃 world。

palm 反過來：它只要輸出一個框，量化的代價落在**信心分數**而不是位置。

## 擋路的是圖被切碎，不是精度

| | palm_detection | hand_landmark |
|---|---|---|
| PReLU | **26** | 0 |
| Conv | 53 | 47 |

`DPUCZDX8G` 沒有 `prelu` 指令，每遇到一個就切一刀，編譯出來 **33 個 DPU 子圖**。
兩個恆等改寫解掉：

```
PReLU(x) = ReLU(x) − a·ReLU(−x)   → ReLU + 兩個 1×1 depthwise conv + Add
零填充 C 個通道                    → 1×1 Conv（單位矩陣列 + 零列）
```

**33 → 6 → 3 段，權重一個沒動、沒有重訓。**

先前兩次嘗試（`fix_prelu_shape.py`、`swap_prelu.py`）把表示法清乾淨了
（637→377 ops、52 個 transpose 歸零）卻**沒有改變切分**——那是「問題在算子種類、
不在圖的雜訊」的證據，所以留著。

## 量化的代價

150 張參考錄影，浮點偵測到 117 張，另 33 張它自己判定沒手（現成的反例）。

```
int8 分數 / float 分數 = 中位數 0.598      ← 整體壓縮，不是打亂
```

| 門檻 | 偵測到 /117 | 誤判 /33 |
|---|---|---|
| 0.50（預設） | 73 | 0 |
| **0.35–0.40** | **85–86** | **0** |
| 0.30 | 99 | 0 |
| 0.20 | 107 | 9 |

**浮點模型在 0.30 誤判 22 張，int8 誤判 0 張**——量化把背景壓得比手更兇，
所以調門檻不是放寬標準。框的中心與浮點差 **0.01 px**（最差 0.28）。

誠實的負面結果：把校正集從 150 張擴增到 450 張重新量化，**每個門檻都更差**
（0.30 時 81 對 99）。`augment_calib.py` 留著記這件事。

## 板上實測

板子已有他人放的 DPU-PYNQ overlay，`ARCH_PP 8 × ICP 10 × OCP 10` ＝ **B1600**，
指紋 `0x101000016010404`。以該指紋重編後：

```
裝置子圖 10（3 DPU、6 CPU、1 輸入）
主幹單段          50.1 FPS
整條偵測器        30.4 FPS
palm 在 A53       11.7 FPS      ← 同一段錄影，只換 palm 跑在哪
palm 在 DPU       21.6 FPS
```

⚠️ **指紋檢查發生在執行時，不是建立 runner 時。** `create_runner()` 會成功，
`execute_async()` 才拋 `fingerprint check failure`。

`nn/dpu/palm/vivado/` 另含自建 B1024 的完整流程（PS + DPU + AXI + 時脈 + 中斷），
Vivado 2025.1 / xck24：LUT 56.6%、DSP 63.9%、WNS +0.500 ns，合成到 bitstream
14 分鐘。**板子沒有跑它**——現成的 B1600 更大且可用。留著是為了 PL 之後要裝
不只一顆 DPU 時，重建的路是我們自己的。

## 這個分支取代了什麼

`feat/direct-pipeline` 的最後一筆（`4c98807`）加的那七個腳本，**這裡全部有，
而且是實際跑出上面數字的版本**。`nn/make_palm_calib.py` 搬到了
`nn/dpu/palm/make_palm_calib.py`。**這個分支併進 main 之後，
`feat/direct-pipeline` 就可以刪。**

## 還沒解的

- **landmark 仍然上不了 DPU。** QAT／蒸餾是唯一沒被排除的路，且是獨立的訓練專案。
- 自建的 B1024 bitstream 沒燒進板子過。
- 校正工具不吃 `--dpu`：XRT 一次只把 `DPU_0` 給一個行程，teleop 佔著時第二個會空等。

板端整合踩到的坑（相機格式、權限、字型）記在 `nn/dpu/palm/board/` 與 vault 的
`Palm_DPU_Board_Integration`。
