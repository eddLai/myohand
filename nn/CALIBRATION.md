# 校正 Calibration

量出**你的手**實際走到哪裡，好讓相機讀到的角度對應到機器手的滿行程。
不校正就用程式內建的預設值——那是別人的手。

---

## 一句話版本

```bash
cd ~/myohand/nn && ../venv/bin/python3 thumb_calib_ui.py 0 5 --save=你的名字-YYYYMMDD
cd ~/myohand/teleop && ./run_teleop.sh --iface=enp17s0 --profile=你的名字-YYYYMMDD
```

teleop 視窗裡按 **CALIBRATE** 按鈕做的是同一件事，只是名字自動給。

---

## 六個姿勢

每個準備 4 秒、**定住不動** 5 秒。指示會用中文大字顯示在視窗上，不必看終端機。
**邊框變綠色＝正在錄，此時手不要動**，直到它跳下一個。

| | 姿勢 | 要注意什麼 | 量出 |
|---|---|---|---|
| P1 | 四指張到最開 | 拇指擺哪都可以 | `CURL_OPEN` |
| P2 | 四指握拳 | 拇指放拳頭**外面**，不要包進去 | `CURL_CLOSED` |
| P3 | 拇指伸直 | 手掌攤平，拇指往旁邊張開，**跟四指同一平面** | `THUMB_OPEN` |
| P4 | 拇指捲曲 | **只折拇指的兩個關節**，指尖倒向食指根 | `THUMB_CLOSED` |
| P5 | 拇指張開成 L 型 | 跟 P3 同一個手型 | `OPP_MIN` |
| P6 | 拇指轉到手心前方 | 拇指**打直**，往小指方向轉；不要真的碰到小指 | `OPP_MAX` |

---

## 最重要的一件事：P4 不是「把拇指壓在手掌上」

`flexion`（拇指彎曲）量的是**拇指自己的兩個關節折了幾度**：

```python
flex = _joint_angle(lm, 1, 2, 3) + _joint_angle(lm, 2, 3, 4)
#                       ↑              ↑
#                    掌指關節 MCP    指間關節 IP
```

**它完全不管拇指指向哪裡。** 拇指抬起、放下、掃過手掌——那些是 `opposition`
和 `abduction` 在管的，不是 `flexion`。

所以 P4 要做的是：

```
手掌攤平朝相機
拇指留在手掌所在的平面上          ← 不抬起、不壓下
只把拇指自己的兩個關節折起來      ← 指尖倒向食指根部
```

自檢：**做的時候拇指不應該離開手掌所在的平面。**

### 這不是吹毛求疵，是踩過的坑

2026-08-06 那次校正，操作者以為在做「拇指彎曲」，實際做的是「拇指從垂直放下
到貼平手掌」——那是**旋轉**。回頭查那五秒的錄影：

```
opposition 走了 124°
flexion    走了  60°
```

量到的 `THUMB_CLOSED` 是旋轉的極限，不是彎曲的。當時沒有任何東西提醒他，
隔天才從錄影裡翻出來。

現在 `thumb_calib_ui.py` 會檢查：P3→P4 之間 `opposition` 飄超過 30° 就
**拒絕寫入**，並告訴你「折拇指的時候不該轉」。

### 附帶好處

拇指留在手掌平面上還有一個好處：**它不會被手掌擋住**。拇指一壓上手掌，
MediaPipe 就看不到它、退回先驗，landmark 直接算錯（實測：壓平時模型仍認為
指尖離手掌平面有 52% 手長高，而「指尖到手腕」的距離是四個姿勢中最遠的）。

**正確的動作剛好也是量得最準的動作。**

---

## 四個入口都會寫進同一個檔

```
camera/calibrate.py          全部六個窗，連續掃動
teleop/teleop_app.py         CALIBRATE 按鈕 → 呼叫 thumb_calib_ui.py
nn/thumb_calib.py            只有拇指，⚠️ 預設 model_complexity=1
nn/thumb_calib_ui.py         全部六個窗，定住姿勢，有動作檢查
```

全部寫進 `camera/calibration.json`，而且**都會把 `active` 搶過去**。

### 揮動式 vs 定住式

按鈕以前是「按一下 → 隨便揮 → 再按一下」，取整段的 **min/max**。
那是對雜訊最敏感的統計量：**一幀爛資料就定生死**，而且每一幀只能把窗撐大、
不能拉回來。

同一批可信幀，兩種估計法的差距：

```
                          min/max 離 p10/p90 多遠
定住姿勢的錄影              0.4 – 3.6°
揮動式的錄影（08/06）      11 – 15°
```

揮動那次的 `flexion` 最高 12 幀是 `116, 102, 101, 101, 100, 99, ...`——
**116 那一幀孤零零高出 14°**，而它通過了 trust gate（被擋掉的 450 幀裡最高
只有 90.8）。可信度閘門攔不住它，min/max 就照單全收。

`thumb_calib_ui.py` 取 p10/p90，兩端各留約一成餘裕，單一爛幀動不了端點。

---

## 模型要一致

`model_complexity` 決定用哪對 `.tflite`：`0` 是 lite、`1` 是 full。
**repo 裡每個執行入口都是 0**（`teleop_app.py`、`calibrate.py`、
`bench_vision.py`、`test_detect.py`），KD240 上也放不下 full。

但 `nn/thumb_calib.py` 的預設值還是 **1**。用它校正就會拿 full 量的窗去餵
lite 跑的 teleop——同一幀 `flexion` 中位數差 **~15°**，八段姿勢全部同向偏移。

```bash
../venv/bin/python3 thumb_calib.py 0 20 --complexity=0    # 要記得帶
```

`thumb_calib_ui.py` 預設就是 0，而且兩個模型同時錄下來供對照。

---

## profile：存得下、切得回

```json
{
  "active": "yuechi-heldpose-20260807",
  "profiles": {
    "yuechi-heldpose-20260807": { "note": ..., "measured": ..., "windows": {...} },
    "pre-thumb-fix-20260806":   { ... }
  }
}
```

- **每存一次開新的一筆**，`save_calibration` 拒絕覆蓋同名的
- 舊的全部留著。`pre-thumb-fix-20260806` 裡那個 `THUMB_CLOSED: 172.8`
  就是靠這樣留下來的——沒有它就無從證明後來改對了
- **`active` 只有一個欄位，誰最後校正誰就搶到**

所以跑 teleop 一律明講自己的 profile，不要靠 `active`：

```bash
./run_teleop.sh --iface=enp17s0 --profile=你的名字-YYYYMMDD
```

`HANDEDNESS` 也存在窗裡面。左手要量成 `"Left"`，不是換個窗就行。

---

## 校正修不好的事

校正只能決定「你的範圍怎麼對應到機器的範圍」。以下是第一層（Google 的權重）
的問題，換誰的手、怎麼校正都一樣：

- **拇指彎曲在閉合端封頂並反轉。** 拇指壓平貼掌時讀數不增反減。
  五種彎曲算法餵同一批 landmark 全部在該段倒退——換公式救不了。
  **沒有任何一筆資料代表「拇指全閉」。**
- **通道洩漏是單向的。** 只掃對掌不彎拇指，`flexion` 跟著飄 42°，
  而 `flexion` 自身訊號只有 17°。
- **手張平時 MediaPipe 認不出左右手。** `LABEL_SURE = 0.85` 這關會擋掉大量
  開掌的幀（P5 實測 150 幀只留 15 幀）。

細節與量測方法見 [`README.md`](README.md) 的「視覺標籤的已知限制」。
