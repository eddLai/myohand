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

## 待辦

- [ ] EMG 與視覺標籤的同步收錄腳本（時間戳對齊）
- [ ] 特徵/視窗設計（libemg 現成特徵 vs 原始波形）
- [ ] 模型（先從小 MLP/TCN 開始）＋訓練腳本
- [ ] 推論端：EMG → pose，接 `hand_fw/hand_api.py` 或未來的 `pid/`
