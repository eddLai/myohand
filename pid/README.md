# pid — 閉環關節控制器（未接上）

以 ANGLEACT 回授修正六軸目標的逐軸積分修正器。`hand_pid.py` 是純修正器
（不碰 EtherCAT、不繞過 `../hand_fw/hand_safety.c` 的 guard），
`test_pid.py` 是離線測試。

**原理、防護規則表、Ki 依據與驗收方法在 vault**：
`L5_HMInteraction/Inspire_RH56F1/01_Hand_Control/PID_Guard/PID_Outer_Loop_Principle.md`

## 現況：不要接上去

**先確認有沒有偏差可修。** `../hand_fw/hand_safety.h:41-71` 的三筆實機讀值
是 1100→1101、1272→1274、1509→1508——960 counts 的行程，誤差 1–2 counts。
而 vault 的驗收判準是「偏差中位數 >40 units 的軸」。**若沒有任何一軸達到那個
門檻，開了只是多一層相位延遲。**

要重新啟用之前，先量出證據：跑幾次 teleop，讀 `runs/<時間戳>/summary.txt`
逐軸的 commanded / actual / absorbed，找出**哪一軸有可重現的偏差**。

> 抖動不是這個模組的問題。症狀是 reference 本身在抖，那是訊號整形，
> 走 [`../filter/`](../filter/)，已經實作並接進 teleop。

## 測試

    python3 test_pid.py        # 離線，73 項
