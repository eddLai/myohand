# filter — 命令平滑與遲滯（開發中）

`nn`/`camera` 吐出的六軸目標到 `hand_fw` 之間的那一格。負責一件事：
**手沒在動的時候，不要送任何動作給馬達**；手在動的時候，不要因此變鈍。

`hand_filter.py` 是出貨的那一份，`measure_jitter.py` 是選參數的量測工具。
**濾波器的實作放在 `hand_filter.py`，`measure_jitter` import 它**——量測的
東西必須就是出貨的東西，兩份拷貝一旦漂移，掃描印出來的每個數字就變成在
量一個沒人在用的程式。這跟 `gains()` 從 live mapping 現算是同一個原則。

已經接進 `teleop_app.py`：內嵌的 EMA 和兩處重複的 deadband 都收掉了，
`hand_sink` 的 deadband 退成 1 count 的防呆下限。跑 teleop 時按 `f` 可以
即時把濾波器移出路徑（還原成完全一樣的舊行為）來當場對照。

## 為什麼需要這一格

症狀是手擺著不動，機器手還是持續小幅抖動。原因在現有的兩層都不夠：

| 現況 | 位置 | 問題 |
|---|---|---|
| EMA 平滑 | `teleop/teleop_app.py:314-318` | 固定的每幀權重，沒吃 `dt` |
| deadband | `teleop/teleop_app.py:406`、`hand_fw/hand_sink.py:231` | 取六軸的 `max()` |

**EMA 的時間常數跟著 frame rate 跑。** 同一個 Smoothing 滑桿（預設 65），
在 30 FPS 是 τ≈100 ms，在 MediaPipe 重新偵測掉到 3.9 FPS 時是 τ≈750 ms
（`test_measure_jitter.py` 量出 8 倍差距）。同一個設定值在筆電和 KD240
上是兩種行為，而且會隨著手有沒有在畫面裡即時變動。

**deadband 讓六軸互相牽連。** `_moved_enough` 是 `max(abs(...))`，
任一軸超過門檻就把整個六軸向量放行——包含只動了 1 count 的那五軸。
所以拇指軸的雜訊會讓四根手指跟著動，而手指正是操作者看著的部分。
合成資料上量到：只有 thumb_rot 有雜訊時，四指仍被指揮走了 1880 counts，
改成逐軸後是 0。

這兩件事互相掩護：deadband 開大會犧牲細動，EMA 開大會變鈍，而 EMA 的
「開多大」本身還不確定。所以要先把兩者拆開量。

## measure_jitter.py

錄一段「手不動」的影片，然後離線分析。**不開任何 sink，跑的時候手不會動。**

    # 手擺在鏡頭前不要動，20 秒
    python3 measure_jitter.py record --device=0 --seconds=20 -o still.csv

    # daemon 有開的話加 --telemetry，順便記手自己的 ANGLEACT 和電流
    python3 measure_jitter.py record --seconds=20 --telemetry -o still.csv

    # 之後隨便掃參數，不用再開鏡頭
    python3 measure_jitter.py analyse still.csv
    python3 measure_jitter.py analyse still.csv --deg=1.0

錄製和分析分開是刻意的：CSV 存的是**原始** mapping 輸出加上產生它的輸入角度，
所以每種濾波、每個門檻都能事後在同一批 frame 上重掃。為了換一個 alpha 而重錄，
比較的會是兩份不同的雜訊實現，什麼都證明不了。

分析會依序回答：

1. **每軸靜止時多吵** — ANGLEACT counts，以及換回輸入角度的度數。
   `gains()` 直接從 `camera/hand_mapping.py` 的視窗常數算增益（四指 6.0、
   thumb_bend 8.6、thumb_rot 7.8 counts/度），所以 mapping 的視窗一改，
   這裡跟著改，不會抄成過期的數字。
2. **現在的 gate 會不會觸發**，以及是**哪一軸**在放行整個向量。
3. **改成逐軸 deadband、EMA、one-euro 會怎樣** — 掃一輪參數比較。

`travel` 是主要指標：整段錄影六軸被指揮移動的總量。手是靜止的，
所以正確答案是 0，任何高於 0 的數字都是被送進馬達的雜訊。
`fingers` 欄單獨列出四指的部分，因為總量會被振幅最大的那一軸蓋過去，
而那不是操作者抱怨的東西。

### 濾波器和 gate 一起掃，不分開掃

`filters x gates` 那張表同一列同時給兩種 gate 的分數，因為**兩者不能疊加**：
濾波器把某一軸壓到它自己的門檻以下，逐軸 gate 就完全不送那一軸，但耦合 gate
還是會在**別的軸**動的時候把它一起放行。合成資料上 `ema tau=0.1s` 的四指
travel 在耦合 gate 是 452、逐軸是 38，十二倍差距；`one-euro fc=0.5 beta=0`
是 41 對 **0**。只看其中一欄會挑錯參數。

`--deg` 決定逐軸那半邊的門檻：一個角度容忍度，乘上各軸自己的 gain。

`p2p_f` 是濾波後四指最大的峰對峰擺幅。`travel` 是路徑長度——對馬達耗損是對的
代理量，對「操作者看到的晃動」不是。濾波器可以砍掉大量路徑長度而擺幅幾乎沒變。
`p2p_f` 降不下來通常代表那是慢漂移，那是真的手在動，不該被濾掉。

### `--telemetry`：手自己的底線

錄製時**照樣不開 sink**，只是多跟 daemon 要 `state`（唯讀，不送 target），
所以手一樣不會動。因此這段 telemetry 是**手閒置時的底線**，不是手對這些
命令的反應——要看反應需要另外錄一段真的在驅動手的。兩個用途：

- 命令雜訊如果低於手自己的 ANGLEACT 雜訊，那是手根本分辨不出來的量，
  濾掉它買不到任何可觀察的東西。
- 閒置電流是零點。之後量「抖動讓馬達做了多少功」時，要拿這個當對照組。

## hand_filter.py

介面是一幀一次，caller 只在被通知時才送：

    filt = hand_filter.HandFilter.for_camera()
    out = filt.update(raw_targets, now)
    if filt.changed:
        sink.send(out)

`out` 永遠是六個值（gate 在擋的時候就是上次放行的那個姿勢），`changed` 說
這次值不值得送。順序是 **one-euro（dt 有 clamp）→ 逐軸遲滯 → hold**。

### 參數，以及它們是哪裡來的

| 參數 | 值 | 依據 |
|---|---|---|
| `MINCUTOFF` | 0.05 Hz | 靜止雜訊有 65–83% 的變異數在 1.8 Hz 以下，是慢速游移不是高頻抖動。基線幾乎凍結（τ≈3.2 s） |
| `BETA` | 0.0005 | 在這個 mincutoff 下**是必要的**：beta=0 量到 450 ms 延遲，有 beta 是 69 ms |
| `DCUTOFF` | 4.0 | 速度估計決定 beta 何時放行。dc=1.0 是 96 ms，dc=4.0 是 69 ms，抖動相同 |
| `DEADBAND_DEG` | 1.5° | 一個角度容忍度乘各軸 gain。四指 9 counts、thumb_bend 13 |
| `DT_MAX` | 66 ms | ≈2× 中位幀時。夾住後 gap 大小完全無影響（實測 122/232/470/634 ms 都是 37.8%），不夾是 44→52% |

教科書的 one-euro 預設值（fc=1.0, beta=0.007）在這裡實測是**錯的**。
`beta=0` 看起來像「簡化成單純低通」的合理清理，實際上會讓延遲變成六倍。

### 這些參數之後一定要再調

現在這組的來源是**一位操作者、一個光線、一台相機、一次 session**
（20 s 靜止 + 30 s 移動），而且**沒有在真手上跑過**。會讓它失效的：

- 換操作者——手的大小和穩定度不同
- 重新校正——`calibrate.py` 一改視窗，gain 就變（門檻會自動跟著改，但雜訊本身不會）
- 換平台——KD240 在 MediaPipe 重偵測時掉到 ~4 FPS，dt 分布跟 .112 的 30 FPS 完全不同
- 換光線、換相機

所以：**參數全部是 constructor 參數，不是寫死的常數**。重調的流程就是重錄
一次 `still.csv` + `moving.csv`，重跑 sweep，把新值傳進去。`analyse` 的
`filters x gates` 表裡永遠有一列 `SHIPPED`，直接讀現在 `hand_filter.py`
的值，所以一眼就看得出「現在裝的這組，在新的雜訊下還合不合理」。

⚠️ 那張表**不能拿來選參數**。靜止資料上平滑越多永遠越好、沒有上限，所以
表裡最好的一列永遠只是「提供的選項裡最兇的那個」。它只說某個設定拿掉多少
抖動；代價是延遲，而延遲不在那張表上。

## run_log.py：每次 teleop 都留下紀錄

不用加任何旗標，跑 teleop 就會在 `runs/<時間戳>/` 留下：

    frames.csv    每一幀
    meta.json     濾波參數、校正 profile、git commit、sink
    summary.txt   人看的那份，退出時直接印出來

**記的是 raw，不是濾波後的。** 一次 live run 只有一條路徑在跑，所以 before/after
不可能是兩次量測——它是**一次量測加一次重建**，而重建是精確的（raw 過舊 gate
是確定性的）。記 raw 還有一個好處：**參數之後會重調，舊的 log 可以拿新參數重新
評分**；記濾波後的輸出只是某個下午設定值的紀錄。

`sent_*` 還是照存，而且不是多餘的——summary 會把 raw 重放一次，**檢查它能不能
重現當時實際送出的值**。沒有別的東西能抓到「這個檔案描述的濾波器已經不是程式碼
裡那個」。

**畫圖不自動跑，只印指令。** matplotlib 絕不能擋在「一次 run」和「它的紀錄」
之間——KD240 只有 1.9 GB RAM，連 mediapipe 都差點裝不下。`summary.txt` 純標準庫
產生，不會失敗。

### 三條曲線裡只有一條是量到的

| 曲線 | 來源 |
|---|---|
| filter OFF | **重建**（raw 過舊 gate） |
| filter ON | **重放**（raw 過出貨的濾波器，且驗證過能重現 `sent_*`） |
| **ANGLEACT** | **量測**——手真正到達的位置 |

第三條回答了前兩條只能暗示的問題：**我們指揮的動作，機構到底執行了多少**。
summary 裡逐軸列出 commanded / actual / absorbed。

兩個但書寫在程式碼裡：它是**每個相機幀取樣一次**，而手每 18–27 ms 才套用一次，
所以**不能拿來當時序參考**；而且它含有 RH56F1 自己的伺服延遲，所以
commanded→actual 的差距是**整條鏈**的，不是這一格的。

## 不屬於這一格的東西

- **slew limit（速率上限）** 屬於 `hand_fw`，跟 `hand_safety.c` 同層。
  它是機構的物理性質，不是某個訊號源的偏好；換掉 filter 或多接一個訊號源
  都不該動到那條保護。而且它對本症狀幫助不大——小振幅高頻雜訊照樣穿過
  速率限制，它擋的是大跳變。

  **這條在 2026-08-07 被重新檢視過，結論不變。** dropout 分析顯示單幀可以
  跳到 370 counts（滿量程 45%），而濾波器**在設計上就不會擋它**——快速的
  真實動作本來就該通過（實測：300 counts 的輸入跳變，一般幀也會放行 38%）。
  所以硬上限仍然需要，仍然屬於 `hand_fw`。這一格負責的是 `DT_MAX`：不讓
  掉幀把濾波器「解除平滑」，那是訊號源的性質，屬於這裡。
- **class-level debouncing** 屬於未來 `nn/` 那條 EMG 路徑。那裡的輸入是
  類別不是位置，需要的是 rejection、majority vote、dwell time
  （libemg 的 `add_rejection` / `add_majority_vote` 現成就有）。
  同一格的兩套實作，不要混為一談。

規劃的介面是「六軸連續目標進、六軸連續目標出」，這樣 EMG 那條可以接成
`nn → class → class debounce → pose lookup → 同一份連續 filter → hand_fw`，
連續濾波只寫一次。

## 待辦

- [x] 在實機錄一段 still.csv（開 `--telemetry`）—— .112，2026-08-07
- [x] 再錄一段「慢慢動」的 —— `moving.csv`，30 s，涵蓋完整 816 counts 行程
- [x] 錄一段有掉幀的 —— `dropout.csv`，4 個 gap，最長 634 ms
- [x] `hand_filter.py`：one-euro ＋ dt clamp ＋ 逐軸遲滯，參數由上面三份數據定
- [ ] **延遲評分工具**。目前最大的缺口：`analyse` 只算 travel，在會動的錄影上
      那個數字沒有意義，所以現在的參數是靠一次性的離線腳本選出來的，沒有
      進 repo。要能重調就必須把它變成常設工具——對 `moving.csv` 量每組參數
      的延遲（最佳時間位移法；實測 cross-correlation 會低估 30–40%、
      onset-crossing 會被雜訊帶偏），跟 travel 併成一張表
- [x] 接進 `teleop_app.py`，把內嵌的 EMA 和兩處重複的 deadband 收掉
- [x] `hand_sink.py` 的 `deadband` 退成防呆下限（1 count）
- [x] 每次 run 自動留紀錄（`run_log.py`），含驅動時的 ANGLEACT 和電流
- [ ] **跑一次有手在畫面裡的 run，然後讀 summary.txt。** 驅動電流的對照組
      就在那裡面等著：閒置基準已經有了（540 幀裡 ANGLEACT 幾乎只有一個
      相異值、電流全程 0，所以我們的雜訊 sd 6–17 counts 比手能分辨的高
      20–50 倍），缺的只是驅動時的那一半
- [ ] **操作者的主觀判斷**。所有延遲數字都是離線指標，而且校驗顯示系統性
      低估約 25%。「手感如何」沒有任何人下過判斷，這是現在最缺的一份資料

## 測試

    python3 test_measure_jitter.py     # 離線，不需要鏡頭或手
    python3 test_run_log.py            # 同上
    python3 test_hand_filter.py        # 同上
