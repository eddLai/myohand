# filter — 命令平滑與遲滯

`nn`/`camera` 吐出的六軸目標到 `hand_fw` 之間的那一格。六軸連續目標進、
六軸連續目標出。負責一件事：**手沒在動的時候，不要送任何動作給馬達**；
手在動的時候，不要因此變鈍。

**量測、參數依據與設計理由在 vault**：`Project_Management/Inspire_RH56F1/
01_Hand_Control/EtherCAT/Command_Filter_Reference.md`。本檔只留「怎麼跑」。

## 怎麼跑

在 .112 上，就這三行：

    ssh ntk112
    cd ~/myohand-feat-filter-stage/teleop
    DISPLAY=:1 ./run_teleop.sh --device 0

`run_teleop.sh` 會自己找直譯器、自己接上已經在跑的 `handd`。**handd 沒在跑**
的話加 `--iface=eno1`，它會順便起一個、結束時再收掉。

視窗開了之後：

| 操作 | 作用 |
|---|---|
| **`A`**（或點 SYNC） | **開始跟隨。不按這個手不會動** |
| `f` | 濾波器 開/關，即時切換來對照。畫面下方顯示現在是哪個 |
| SETTINGS → Deadband | 邊跑邊調容忍度（度） |
| `q` | 結束，並把這次的 summary 直接印出來 |

不想碰手、只想看視覺那半邊：加 `--sink=none`。

## 每次跑完的紀錄

自動寫進 `../runs/<時間戳>/`（已 gitignore）：

    frames.csv    每一幀：raw 目標、實際送出、ANGLEACT、電流、增益戳記
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

> ⚠️ `filters x gates` 那張表**不能拿來選參數**。靜止資料上平滑越多永遠越好、
> 沒有上限，所以表裡最好的一列永遠只是「提供的選項裡最兇的那個」。它只說某個
> 設定拿掉多少抖動；代價是延遲，而延遲不在那張表上。

## 檔案

| 檔案 | 內容 |
|---|---|
| `hand_filter.py` | **出貨的那一份**。one-euro（dt 有 clamp）→ 逐軸遲滯 → hold |
| `measure_jitter.py` | 選參數的量測工具。**import `hand_filter`**，不自己留一份 |
| `run_log.py` | 每次 teleop 的紀錄；也可單獨跑來重生 summary |

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

- [ ] **延遲評分工具進 repo**。現在的參數是靠一次性離線腳本選出來的
- [ ] **跑一次有手在畫面裡的 run，讀 summary.txt** — 驅動電流的對照組在裡面
- [ ] **操作者的主觀判斷**。「手感如何」沒有任何人下過判斷，這是最缺的
