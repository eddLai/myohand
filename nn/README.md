# nn — EMG→pose 模型（規劃中，尚未實作）

把前臂 EMG 訊號映射成 RH56F1 的手勢目標。目前這個資料夾只有規劃，
還沒有程式。

## 資料流（規劃）

```
EMG (Myo, 8ch)          labels (視覺)                    輸出
data_recording.py  ──►  camera/hand_mapping.py      ──►  [pinky, ring, middle,
../data/*.csv           thumb_features(): {flexion,       index, thumb_bend,
                        abduction, opposition} + curls    thumb_rot] 0..2000
```

- **輸入**：滑動視窗的 EMG 特徵（收集端在 repo 根目錄的 libemg 管線，
  資料集在 `../data/`）。
- **標籤**：同步錄影經 `camera/hand_mapping.thumb_features()` 與
  `finger_curl()` 產生的分離通道（度數）。拇指三通道
  {flexion, abduction, opposition} 互不滲漏（`camera/test_mapping.py`
  已驗證），abduction 沒有對應的機器手軸但仍應當作標籤學。
  只用 `thumb_trust()` 判定可信的幀當標籤——被遮擋/翻轉幀是幻覺。
- **輸出**：六軸目標（機器手慣例 0 閉合、2000 張開），或先輸出
  角度通道再由 `hand_mapping` 的視窗換算成目標。

## ⚠️ 視覺標籤的已知限制（2026-08-07 實測）

上面的資料流以 `thumb_features()` 的度數當標籤。在**真實影像**上量過之後，三個通道
並非等價可信——標籤歪掉，之後訓練的模型會一起歪。

量測：`thumb_steps.py` 八段固定姿勢各握住 5 秒（每段約 150 幀），同批畫面同時餵給
兩個模型。分析腳本見同資料夾。

| 通道 | 狀態 |
|---|---|
| `opposition` 對掌 | 可用。四個掃掌位置 `SEPARATED`，姿勢內四分位距僅 2–4° |
| `abduction` 外展 | 穩定，但沒有對應的機器手軸 |
| `flexion` 彎曲 | 兩個問題，見下 |

**1. 彎曲在閉合端封頂並反轉。** 拇指壓平貼掌（最閉合）時量到的彎曲不增反減——換算成
機器行程是 打直 38% → 彎 1/3 73% → 彎 2/3 **95%** → **壓平貼掌 66%**（lite）。

根因不在幾何：拇指尖離手掌平面的高度，壓平時實測 **52%**（物理上應接近 0），且「拇指尖
到手腕」的距離在壓平時是四個姿勢中**最遠**的。也就是 landmark 本身就錯——拇指一貼上
手掌就被遮住，模型退回它學到的手部先驗。`flex_test.py` 以同一批 landmark 重播五種彎曲
算法（`mcp+ip`／`mcp`／`ip`／拇指鏈長比／尖到掌心距離），**全部在該段倒退**。吃同一組
錯的點，換公式救不了。

→ **沒有任何一筆標籤代表「拇指全閉」。**

**2. 通道洩漏是單向的。** 只掃對掌、不彎拇指時，量到的 `flexion` 跟著飄 **42°**（lite；
full 31°），而 `flexion` 自身的訊號範圍只有 **17°**——汙染比訊號大 2.5 倍。反向（彎拇指
汙染對掌）僅 10–16°，可接受。

> `camera/test_mapping.py` 的「三通道互不滲漏」仍然成立，但它餵的是**合成骨架**，驗證的
> 是映射程式碼的旋轉不變性。真實影像的遮擋與深度誤差不在那個測試的涵蓋範圍內。

**3. 校正窗與執行用的模型不一致。** 目前 `calibration.json` 的拇指窗量自
`model_complexity=1`，而 repo 內每一支程式（含 `teleop_app.py:256`）都是 `0`。同幀
flexion 中位數差 **11.5°**，所有標籤整體偏移。

**4. 上面資料流圖裡的 `0..2000` 已經過時。** 2026-08-06 的刻度修正後，目標範圍是
`hand_scale.TARGET_MIN 890`（全閉）到 `TARGET_MAX 1850`（全開），且 `hand_mapping` 的
`T_MIN` 是 1034——遙控端刻意不送到全閉。輸出層的標籤定義要跟著改。

### 收標籤之前建議先處理

- [ ] 用 `model_complexity=0` 重新校正，或全 repo 統一為 `1`
- [ ] 彎曲標籤只取「彎到 2/3 以內」的區段，或改用不依賴遮擋段的量
- [ ] 查 42° 洩漏的機制；在那之前 `flexion` 與 `opposition` 不宜視為獨立標籤

## 待辦

- [ ] EMG 與視覺標籤的同步收錄腳本（時間戳對齊）
- [ ] 特徵/視窗設計（libemg 現成特徵 vs 原始波形）
- [ ] 模型（先從小 MLP/TCN 開始）＋訓練腳本
- [ ] 推論端：EMG → pose，接 `hand_fw/hand_api.py` 或未來的 `pid/`
