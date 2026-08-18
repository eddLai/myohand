# filter — 命令平滑與遲滯

`nn`/`camera` 吐出的六軸目標到 `hand_fw` 之間的那一格。六軸連續目標進、
六軸連續目標出。負責一件事：**手沒在動的時候，不要送任何動作給馬達**；
手在動的時候，不要因此變鈍。

**量測、參數依據與設計理由在 vault**：`Project_Management/Inspire_RH56F1/
01_Hand_Control/Command_Filter/`（`Command_Filter_Reference.md` 是 hub，另有
`Jitter_Instrument_Reference.md`、`Run_Log_Reference.md`）。本檔只留「怎麼跑」。

## 怎麼跑

在 .112 上，就這三行：

    ssh ntk112
    cd ~/myohand-feat-filter-stage/teleop
    DISPLAY=:1 ./run_teleop.sh --device 0

`run_teleop.sh` 會自己找直譯器、自己接上已經在跑的 `handd`。**handd 沒在跑**
的話加 `--iface=enp17s0`，它會順便起一個、結束時再收掉。

視窗開了之後：

| 操作 | 作用 |
|---|---|
| **`A`**（或點 SYNC） | **開始跟隨。不按這個手不會動** |
| `f` | 濾波器 開/關，即時切換來對照。畫面下方顯示現在是哪個 |
| SETTINGS → Deadband | 邊跑邊調容忍度（度）。**動它不會毀掉這份紀錄**，見下 |
| `q` | 結束，並把這次的 summary 直接印出來 |

不想碰手、只想看視覺那半邊：加 `--sink=none`。

## 每次跑完的紀錄

### 一份 run log ＝ 一段連續的濾波器

**一次 teleop 可能留下不只一份。** 按 CALIBRATE 或換相機的時候，濾波器會被
**重建**（不是只清狀態），現在這一份就收起來、開新的一份繼續記：

    runs/2026-08-10T20-00-00/     校正前
    runs/2026-08-10T20-06-30/     校正後 ← 通常是你要看的那份

兩個理由。**校正換掉了增益**——deadband 是「度 × 該軸增益」，2026-08-07 那次校正
把 thumb_rot 從 7.80 改成 4.59，沿用舊表等於滑桿寫 1.5 度、實際在用 2.55 度。
**而 summary 的 `filter ON` 那一欄是重放算出來的**，重放不可能知道中途清空過，
所以跨過清空的紀錄自己驗不了自己（`replay check` 會直接說 `DOES NOT MATCH`）。
切在清空點，兩邊就完全一致——剛 reset 的濾波器和剛建好的濾波器沒有任何差別。

前面那一段照樣寫出來，不丟：`frames.csv` 本來就在硬碟上了，而**讓你按下 CALIBRATE
的往往正是那一段**。

### 但動 Deadband 滑桿不是切割

滑桿只換一個門檻、不清任何狀態，濾波器的記憶是連續的，所以**紀錄不切**——
`deg` 逐幀記成一欄，重放照著那一欄走。**放心邊跑邊調**，那份 run 照樣驗得了
自己（600 幀中途 1.5→3.0 度：修好前 322/600 不符、最差 20.8 counts，修好後
600/600 全等）。summary 開頭那行會寫出實際跑過的範圍，例如
`deadband=1.5-3 deg (the slider moved during the run)`。

### 每份裡面有什麼

自動寫進 `../runs/<時間戳>/`（已 gitignore）：

    frames.csv    每一幀：raw 目標、實際送出、ANGLEACT、電流、增益戳記、deg
    meta.json     濾波參數、校正 profile、git commit、sink
    summary.txt   人看的那份，退出時直接印出來

**畫圖不自動跑**，summary 最後一行就是可以複製貼上的指令。

## 量測工具

錄一段影片，然後離線分析。**不開任何 sink，跑的時候手不會動。**

    cd ~/myohand-feat-filter-stage/filter

    # 手擺在鏡頭前不要動，20 秒；daemon 有開就加 --telemetry
    python3 measure_jitter.py record --device=0 --seconds=20 --telemetry -o still.csv

    # 之後隨便掃參數，不用再開鏡頭
    python3 measure_jitter.py analyse still.csv
    python3 measure_jitter.py analyse still.csv --deg=1.0     # 換一個角度容忍度

    # 疊圖：同一批 frame 跑兩條路徑
    python3 measure_jitter.py plot still.csv moving.csv 'dropout.csv@14.6:4' \
        --axis pinky ring middle index -o ab.png

`analyse` 只需要標準庫；`plot` 才要 matplotlib，`record` 才要相機。

**上面用的 `still.csv`、`moving.csv`、`dropout.csv` 就在這個資料夾裡**，是 2026-08-07
在 `.112` 上一位操作者 ~30 FPS 錄的（596／897／897 幀）。它們不是範例檔名——
`hand_filter.py` 的 `MINCUTOFF` 與 `DT_MAX` 就是從這三份算出來的，
`test_hand_filter.py` 裡寫死的四個空隙長度（0.122／0.232／0.470／0.634 秒）也是
`dropout.csv` 裡實際那四個。**重錄一次會得到不同的資料**，那些數字是這三份特定
錄影的性質，所以它們留在版控裡，改動濾波器時才有同一份東西可以重跑。

## 濾波器本身長什麼樣：頻率響應與相位

    python3 plot_response.py     # 只要標準庫，不用 matplotlib、不用相機

寫出兩張跟著佈景主題走的 SVG（亮底暗底都讀得了）：

| 檔案 | 回答的問題 |
|---|---|
| `response_gain.svg` | 某個頻率的抖動，**剩多少**送到馬達 |
| `response_phase.svg` | 同一個頻率**晚多久**出來 |

**one-euro 不是 LTI，所以沒有「一條」頻率響應**，只有一族，每條對應一個被凍住的
cutoff。圖上四條的速度不是編出來的好看數字，是把出貨的 `_OneEuro` 真的跑過
`still.csv` / `moving.csv` 之後，它自己估出來的 `dx_hat`：靜止中位數 124、移動
中位數 1268、移動 p95 5505 counts/s，換算 cutoff 0.05 / 0.11 / 0.68 / 2.80 Hz。
橘色虛線是被取代的那個固定 EMA（31 FPS 下 2.07 Hz）。

看得到的三件事：

- **beta 是整個設計**。手不動時濾波器幾乎凍住（tau 3.2 s），動到 p95 時已經開了
  56 倍，比它取代的固定 EMA 還寬——「靜止時很兇、動起來讓開」在圖上就是這族曲線
  的張開幅度
- **相位落後有上限，而且在 Nyquist 回到 0**。最差 −82 度，不是課本連續版的 −90
  漸近線；`H(z)` 在 fs/2 是實數。這是這段離散程式碼的性質，不是近似
- **相位圖不能單獨看**。0.05 Hz 那條在 1 Hz 落後 81 度（226 ms），但它同時把
  1 Hz 砍掉 26 dB——晚到的東西幾乎等於沒有

速度那幾個數字繼承了下面「待辦」那條：它們是 `ROT_MIN=1226` 時代錄的，
`d1bb2bd` 之後 thumb_rot 的 counts 尺度是 1.54 倍，所以那一軸落在圖上哪一條
現在不一樣了。重錄之後 `plot_response.py` 直接再跑一次就會更新。

**兩張圖都只畫到第一段。** 遲滯是硬非線性，門檻以下的正弦出來是「靜音」而不是
「被衰減的正弦」，畫不進頻率響應裡。也**不要把相位延遲當成選參數時用的那個延遲**：
`hand_filter.py` 裡的 69 ms 是階躍/追蹤指標，跟每頻率的相位延遲是兩個問題。

> ⚠️ `filters x gates` 那張表**不能拿來選參數**。靜止資料上平滑越多永遠越好、
> 沒有上限，所以表裡最好的一列永遠只是「提供的選項裡最兇的那個」。它只說某個
> 設定拿掉多少抖動；代價是延遲，而延遲不在那張表上。

## 檔案

| 檔案 | 內容 |
|---|---|
| `hand_filter.py` | **出貨的那一份**。one-euro（dt 有 clamp）→ 逐軸遲滯 → hold |
| `measure_jitter.py` | 選參數的量測工具。**import `hand_filter`**，不自己留一份 |
| `run_log.py` | 每次 teleop 的紀錄；也可單獨跑來重生 summary |
| `plot_response.py` | 畫上面那兩張 SVG。同樣 **import `hand_filter`**，畫的是出貨的那份 |

## 測試

    python3 test_hand_filter.py        # 三個都是離線，不需要鏡頭或手
    python3 test_measure_jitter.py
    python3 test_run_log.py

## 不屬於這一格的東西

- **slew limit（速率上限）** 屬於 `hand_fw`，跟 `hand_safety.c` 同層。這一格
  負責的是 `DT_MAX`：不讓掉幀把濾波器「解除平滑」。
- **class-level debouncing** 屬於未來 `nn/` 那條 EMG 路徑（rejection、
  majority vote、dwell time）。同一格的兩套實作，不要混為一談。

規劃的介面是「六軸連續目標進、六軸連續目標出」，這樣 EMG 那條可以接成
`nn → class → class debounce → pose lookup → 同一份連續 filter → hand_fw`。

## 待辦

### 待確認：thumb_bend 的行程是不是真的就是這樣

`camera/hand_mapping.py` 的 `BEND_MIN, BEND_MAX = 1140, 1375` 是 2026-08-07
從八份 run log **量出來**的，而且驗證過（`runs/*_open_close`、`runs/*_hold`：
absorbed 82%→24%、峰值電流 1397→95 mA、STA 沒再出現堵轉碼 5/6）。

**但那是「操作者的手勢碰巧掃到的極限」，所以只是行程的下界，而且只對這一台。**

- [ ] **逐軸 sweep 工具**。慢速把每一軸推向兩端、偵測到 ANGLEACT 不再變化就
      **立刻退開**，記錄停止點。這是 `hand_fw/hand_safety.h:63-68` 記著
      「per-axis limits need a measured sweep, which has not been done」的
      那件事。要點：
      - 絕不能停留在停止點——閉端頂著推就是 1.4 A（已實測）
      - 用 ANGLEACT 停止變化 ＋ 電流上升雙重判斷，不要只看其中一個
      - 開端和閉端不對稱：thumb_bend 開端頂著是 0 mA（無害），閉端是 1.4 A
      - 結果要能直接餵回 `hand_mapping.py`，不要用抄的
- [ ] **確認 thumb_bend 開端 1375 是機械停止還是別的**。證據是「命令 >1500
      持續 10 秒、ANGLEACT 停在 1375、電流 0」——電流 0 表示馬達根本沒在推。
      是到底了，還是韌體判定到位了？sweep 應該能分辨
- [ ] **其他五軸也還沒 sweep 過**。四指觀測到 1060–1804、thumb_rot 892–1845，
      都沒有堵轉跡象，所以不急，但同樣是下界不是行程
- [x] ~~`ROT_MIN` 1226 vs ~600 未調和~~ — 2026-08-07 `d1bb2bd` 結案，
      `nn/rot_floor_probe.py` 一步步降到 890 每步都到位、電流最低點就在底部，
      所以 890 是尺度的下限不是機構的。現在 `ROT_MIN = hand_scale.TARGET_MIN`

### 離線就能做（不需要手）

- [x] ~~**`deg` 要逐幀記，重放照著走**~~ — `deg` 成為 `frames.csv` 的第 50 欄，
      重放逐幀照著它設門檻。滑桿是常態旋鈕、不清狀態，所以**不切紀錄**
- [x] ~~**replay check 不要跳過 `mode=off` 的幀**~~ — gate 拿掉了，整段關著飛
      的 run 現在也驗。那些幀上驗的是「紀錄與程式一致」而非「與當時飛的一致」，
      summary 會自己說出這句
- [x] ~~**換相機的分支補 `last_sent = None`**~~ — 與 `run_calibration` 對齊
- [ ] **逐軸 sweep 工具先寫好放著**（要點見上面「待確認」那節）。**寫完就等
      `.112` 回來跑一次**，而不是那天才開始想
- [ ] **延遲評分工具進 repo**。現在的參數是靠一次性離線腳本選出來的，
      沒有它就沒辦法重調。`runs/` 裡留著的四份有 raw 也有 ANGLEACT，
      離線就能開工；但第一件事是說清楚它在量什麼、跟什麼校準過

### 要手

- [x] ~~跑一次有手在畫面裡的 run~~ — 2026-08-07，驅動電流已量到
- [x] ~~**校正流程真手驗證**~~ — 2026-08-10 走完一次，五項都成立：兩份 run log
      （`runs/2026-08-10T17-20-06_before_calibrate` 與 `..T17-21-14_after_calibrate`）、
      後面那份的 profile 是新的 `session-20260810-172019` 且六軸增益全變
      （thumb_rot 7.06→12.36、thumb_bend 2.91→6.42、四指 4.99→6.02）、帶
      `follows` 一行與一份 `calibration.txt`、兩份 replay 都 `worst 0.0 counts`。
      ⚠️ **那份 profile 本身量得偏淺**，六個窗有五個被工具標「這次做得夠滿嗎」，
      拇指彎曲窗寬從 80.8° 掉到 36.6°。當流程證據可以，要日常用該重校一次
- [ ] **在現在的 mapping 下重錄 still/moving**。參數是在 `ROT_MIN=1226` 時代
      選的，`d1bb2bd` 之後 thumb_rot 的 counts 尺度是 1.54 倍，`BETA` 在那一軸
      的實際行為跟著變了，沒人量過
- [ ] **操作者的主觀判斷**。目前只有一句「感覺還行」，而操作者本人傾向看圖
      判斷。延遲的數字全是離線指標且校驗顯示低估約 25%，所以「鈍不鈍」
      仍然沒有可靠的答案
