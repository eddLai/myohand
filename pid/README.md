# pid — 閉環關節控制器（規劃中，尚未實作）

以 ANGLEACT 回授修正六軸目標的閉環控制。目前只有規劃，還沒有程式。

## 為什麼還不能做成即時迴路

RH56F1 韌體**在 master 斷線後才執行姿勢**（見 `../hand_fw/README.md`
的 axis semantics 一節）：`hand_set` 一趟要 2–3 秒，且執行期間拿不到
連續回授。真正的即時 PID 要等 vendor F1 文件揭露 realtime 執行
trigger 後才可能。

## 現在就能做的部分（規劃）

- **離線/慢速外環**：每趟 `hand_set` 都會回報 `ANG=[...]`（teleop 已
  拿它畫 reached 刻度）。可以做每趟一次修正的慢速積分外環：
  target' = target + Ki·Σ(target − ANGLEACT)，補償各軸的穩態偏差。
- **介面約定**：輸入六軸目標（0..2000）＋上一趟 ANGLEACT 讀回；
  輸出修正後目標，仍經 `hand_fw/hand_safety.c` 的 guard 層——
  控制器永遠不得繞過安全層直接寫 PDO。
- **模擬**：先用一階模型＋量測到的 ANG_CLOSED/ANG_OPEN span
  （`camera/hand_mapping.py` 的 890..1850）調參。

## 待辦

- [ ] 讀回資料的紀錄格式（每趟 target/ANG/STA/電流）
- [ ] 慢速外環 prototype（掛在 teleop 或 hand_api 的送出路徑）
- [ ] vendor realtime trigger 文件到手後：真 PID 內環
