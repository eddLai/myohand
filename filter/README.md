# filter — 命令平滑與遲滯（開發中）

`nn`/`camera` 吐出的六軸目標到 `hand_fw` 之間的那一格。負責一件事：
**手沒在動的時候，不要送任何動作給馬達**；手在動的時候，不要因此變鈍。

目前只有量測工具（`measure_jitter.py`），還沒有 `hand_filter.py`。
參數要從真實雜訊數據來，所以先量再寫。

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

## 不屬於這一格的東西

- **slew limit（速率上限）** 屬於 `hand_fw`，跟 `hand_safety.c` 同層。
  它是機構的物理性質，不是某個訊號源的偏好；換掉 filter 或多接一個訊號源
  都不該動到那條保護。而且它對本症狀幫助不大——小振幅高頻雜訊照樣穿過
  速率限制，它擋的是大跳變。
- **class-level debouncing** 屬於未來 `nn/` 那條 EMG 路徑。那裡的輸入是
  類別不是位置，需要的是 rejection、majority vote、dwell time
  （libemg 的 `add_rejection` / `add_majority_vote` 現成就有）。
  同一格的兩套實作，不要混為一談。

規劃的介面是「六軸連續目標進、六軸連續目標出」，這樣 EMG 那條可以接成
`nn → class → class debounce → pose lookup → 同一份連續 filter → hand_fw`，
連續濾波只寫一次。

## 待辦

- [ ] 在實機錄一段 still.csv（開 `--telemetry`），取得真實的每軸雜訊數字
- [ ] 再錄一段「慢慢動」的，因為 travel 只評得出靜止表現，評不出延遲
- [ ] 錄一段有掉幀的（手移出畫面再移回、遮擋）。one-euro 的
      `alpha = 1/(1+tau/dt)` 在 dt 變大時趨近 1，等於**幾乎不濾**——
      MediaPipe 重新偵測的那一瞬間姿態最不可靠，濾波卻最弱。
      偵測乾淨連續的 still.csv 永遠照不出這件事。
- [ ] 手端驅動對照：命令一個固定姿勢 hold 住、記電流，跟 `--telemetry`
      的閒置電流比。這是「抖動到底讓馬達做了多少功」唯一誠實的量法，
      也是這一格的正當性數字
- [ ] `hand_filter.py`：逐軸遲滯 ＋ 吃 `dt` 的平滑，參數由上面兩份數據定
- [ ] 接進 `teleop_app.py`，把內嵌的 EMA 和兩處重複的 deadband 收掉
- [ ] `hand_sink.py` 的 `deadband` 退成防呆下限，不再是主要的抖動防線

## 測試

    python3 test_measure_jitter.py     # 離線，不需要鏡頭或手
