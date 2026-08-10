# myohand

用前臂 EMG 驅動 Inspire RH56F1 靈巧手。Repo 依功能拆開，每個資料夾有自己的 README，細節寫在那裡。

| 目錄 | 內容 | 狀態 |
|---|---|---|
| `emg/` | Myo 臂環擷取，靠 libemg | 可錄資料，還沒有分類器 |
| `camera/` | MediaPipe 手部追蹤 -> 關節角度分解 (`hand_mapping.py`)、校正、mapping 測試 | 可動 |
| `teleop/` | Webcam teleop app：儀表板 UI、SYNC/CALIBRATE/HAND 控制。每次跑完在 `runs/` 留一份紀錄 | 可動 |
| `hand_fw/` | RH56F1 的 EtherCAT 控制堆疊：C core (SOEM)、安全層、Python API + HTTP server + 常駐 daemon。操作說明在它的 README，技術細節在 vault | 可動 |
| `filter/` | 送給手之前的命令平滑與遲滯：靜止時不要動，動起來不要鈍。已接進 teleop | 可動，參數待重調，見其 README |
| `nn/` | EMG -> 姿態的網路 (labels 來自 `camera/hand_mapping.thumb_features`) | 規劃中，見其 README |
| `pid/` | 對 ANGLEACT 回授做閉迴路關節控制 | 擱置——2026-08-07 量到穩態誤差只有 1–2 counts，沒有東西給它修，見其 README |
| `data/` | 已錄好的手勢資料集 | 見下 |
| `libemg/` | submodule（LibEMG/libemg） | |

EMG 那半邊還沒有任何程式碼會去呼叫 `hand_fw/`，webcam teleop 也不讀 EMG。
把兩邊接起來需要一個從 EMG 視窗吐出手勢類別的分類器，那部分還不存在（`nn/` 的規劃）。

接下來要做的三件事（EMG 整合、部署 KD240、部署前瘦身）以及它們彼此擋著誰，
見本檔最後的[「接下來」](#接下來)。各資料夾自己的待辦仍在各自的 README。

## Quickstart

**在 .112（`ssh ntk112`，手接在那台）上跑 teleop**，checkout 在
`~/myohand-feat-filter-stage`：

    cd ~/myohand-feat-filter-stage/teleop
    DISPLAY=:1 ./run_teleop.sh --device 0        # handd 沒在跑就加 --iface=enp17s0

`run_teleop.sh` 自己找直譯器、自己接上已經在跑的 `handd`（沒有就起一個，
結束時收掉）。視窗開了之後**要按 `A` 或點 SYNC 才會開始跟隨**，`f` 切換
濾波器開關來當場對照，`q` 結束並印出這次的 summary。細節見
[`filter/README.md`](filter/README.md)。

視窗上還有哪些按鈕、校正怎麼跑、卡住了怎麼辦 —— 見
[`teleop/README.md`](teleop/README.md)。第一次校正前先看
[`nn/CALIBRATION.md`](nn/CALIBRATION.md)。

每跑一次就在 `runs/<時間戳>/` 留下 `frames.csv`、`meta.json` 和
`summary.txt`。**純時間戳的目錄不進 git**；要留下來的就給它一個帶底線後綴的
描述性名字，那種會被追蹤。哪幾份留著、為什麼，見
[`runs/README.md`](runs/README.md)。

**第一次架這台機器**（或換一台）：

    ./setup.sh                                    # venv + SOEM + C binaries + caps（sudo 一次）
    ECAT_IFACE=enp17s0 ./hand_fw/hand_ctl state      # 遙測，不會動；先確認手在線上

網卡名每台不一樣（`.112` 是 `enp17s0`，`.28` 是 `eno1`，KD240 依線接在哪是
`eth0`/`eth1`/`eth2`）——**先用 `ecat_scan` 列出來，不要猜**。
`hand_ctl` / `hand_set` 讀 `$ECAT_IFACE`（預設 `eth0`），`handd` 用 `--iface=`。
給錯的話錯誤訊息是 `need CAP_NET_RAW or root`，看起來像權限問題，其實是網卡名。

### 直譯器

`run_teleop.sh` 自己找，順序是
`$TELEOP_PYTHON` → `../venv` → `$HOME/myohand/venv` → `python3`。
在 .112 上它落在 `$HOME/myohand/venv`（mediapipe 0.10.21、cv2 4.11），
所以從 `myohand-feat-filter-stage` 這個 checkout 跑也不用設任何東西。

**它不看 `$PATH`**，所以只 `conda activate` 是沒用的——環境在別處的話要
明講：

    TELEOP_PYTHON=/path/to/bin/python DISPLAY=:1 ./teleop/run_teleop.sh --device 0

這個 fallback 順序有個陷阱：`$HOME/myohand/venv` 可能是**別人 checkout 的
環境**，而且 import 得過，於是靜靜地跑錯直譯器而不報錯。

直接叫 Python 進入點時要自己給路徑，例如
`~/myohand/venv/bin/python3 hand_fw/hand_api.py open`；`camera/` 底下的
獨立腳本要從那個資料夾執行（`cd camera && ...`）。

該主機的其他細節（`DISPLAY=:1`、caps）見
[`hand_fw/README.md`](hand_fw/README.md) 的「跑整條鏈」。

## emg/

| 檔案 | 用途 |
|---|---|
| `record_gestures.py` | 用 libemg 的錄製 GUI 跑 37 手勢 × 3 reps（每 rep 5 秒、休息 10 秒），寫進 `data/` |
| `myo_monitor.py` | tkinter 診斷面板：即時看 8 通道 EMG 與 IMU，可切 raw/filtered、震動、斷線 |
| `inspect_recording.py` | `#%%` cell 形式，把單一 CSV 讀成 DataFrame 來看 |

路徑都錨在 repo 根目錄，所以從哪個 cwd 執行都可以。

### 環境

需要 Python 3.11（`numpy<2` 沒有 cp313 wheel）：

    conda create -n myo python=3.11
    conda activate myo
    pip install -r requirements.txt
    git submodule update --init
    pip install ./libemg          # 不要加 -e，理由見 requirements.txt

BLED112 dongle 會被 `Myo.detect_tty()` 自動找到（比對 `PID=2458:0*1`），不用設 port；
臂環自己的 micro-USB 只能充電。第一次連線可能要掃約 2000 個封包，之後很快。

**地雷**：`myo_monitor.py:57` 的 `myo.bt.ser.timeout = 0.001` 會讓任何等 ack 的指令
（`vibrate()`、`disconnect()`）以 `AttributeError: 'NoneType' object has no attribute 'typ'`
炸掉，因為 `recv_packet()` 逾時回傳 None。這個 timeout 要在那些呼叫**之後**才設，或是自己包 guard。

## data/

`gestures_set/` 是 37 張手勢提示圖（約 127MB），已被 gitignore，
`gui.download_gestures()` 會重新下載。

錄製結果（`grandma's_left_hand_emg/`、`grandma's_right_hand_emg/`）是 libemg 的
`C_<class>_R_<rep>_emg.csv` 格式，class 對應表在同目錄的 `collection_details.json`。

## hand_fw/

怎麼跑見 [`hand_fw/README.md`](hand_fw/README.md)。技術細節都在 ExoPulse_docs vault
的 `Project_Management/Inspire_RH56F1/01_Hand_Control/EtherCAT/`：`Hand_FW_Reference`
（控制層、觸發策略、延遲、尺度推導、安全層、幾何表、teleop UI、已知限制）、
`Execution_Trigger_Settled`（執行觸發結論）、`SOEM_Port_Plan`（移植與建置陷阱）；
完整 bring-up 流水帳在 `Inspire_RH56F1_Hand_Bringup_Ops_Log`。

## 接下來

這一節只放**跨資料夾、而且還沒開始**的三件事，以及它們彼此擋著誰。各資料夾自己
的待辦寫在各自的 README（`filter/`、`nn/`、`pid/`）。

推導、量測依據與整體順序在 ExoPulse_docs vault 的
`Project_Management/Inspire_RH56F1/`：`Inspire_RH56F1_KD240_Deployment_Plan`
（§九 是這三件事的六步順序）、`03_Hand_Pose_Model/MediaPipe_Replacement_Interface_Fork`、
`Inspire_RH56F1_Repo_Slimming_Plan`。這裡只留 repo 這一側「動哪些檔案」。

### 現在的位置

| | 狀態 |
|---|---|
| 視覺 teleop 整條鏈 | **通了**。camera → `hand_mapping` → `filter` → `handd` → RH56F1，2026-08-10 在 `.112` 錄到真的 ANGLEACT 與電流（`runs/2026-08-10T15-31-58_ecat_enp17s0`） |
| EMG | 只到「錄得起來」。沒有分類器，`emg/` 裡沒有一行程式碼會呼叫 `hand_fw/` |
| KD240 | 只驗過 C 那一段（2026-08-06、`.228`、`eth1`、手動冒煙）。視覺那一段沒上過板 |
| `pid/` | 擱置，沒有偏差給它修（穩態誤差 1–2 counts / 960 counts 行程） |

`feat/filter-stage` 比 `main` 多 **51 個 commit** 還沒合。下面三件事都會動到同一批
檔案，所以這是共同前置。

### 1. Myo EMG 手環整合

目標：EMG → 手勢 → 六軸目標，接進**跟 teleop 同一條下游**。

    nn → class → class debounce → pose lookup → 同一份 filter → hand_fw

介面已經先留好了：`filter/` 是「六軸連續目標進、六軸連續目標出」，換來源不用改它。

**現在有的**：`emg/record_gestures.py`（libemg 錄製 GUI，37 類）、`myo_monitor.py`
（8 通道 + IMU 診斷面板）、`data/` 兩份錄好的資料集。

**缺的，依序**：

- [ ] **先修標籤，再收資料。** `nn/README.md` 記了三件在真實影像上量到的問題：
      校正窗量自 `model_complexity=1` 而執行時全是 `0`（同幀 flexion 中位數差
      **11.5°**）、flexion 在閉合端封頂並反轉（沒有任何一筆標籤代表「拇指全閉」）、
      opposition → flexion 單向洩漏 **42°** 而 flexion 自身訊號範圍只有 **17°**。
      **標籤歪，之後訓的模型會一起歪。** 這一段不需要手也不需要臂環。
      ⚠️ 但先看第 2 項的「分岔點」——視覺模型要是會換，這件事會白做一次。
- [ ] **同步收錄腳本**（EMG 與影像時間戳對齊）。目前 `data/` 是純 EMG，沒有對應影格。
      順便：磁碟上每類只有 `R_0` 一個 rep，`collection_details.json` 寫的是 3。
- [ ] **特徵／視窗設計**。libemg 現成特徵先當 baseline，再談原始波形。
- [ ] **模型 ＋ 訓練腳本**。先小 MLP/TCN。
- [ ] **推論端**：EMG → pose，接 `hand_fw/hand_api.py`。
- [ ] **class-level debounce**（rejection / majority vote / dwell time）。這一格屬於
      EMG 路徑，**不要跟 `filter/` 的逐軸遲滯混為一談**——同一格的兩套實作。
- [ ] **誤判下手會做什麼，沒人量過。** EMG 誤判是「連續的錯姿勢」，不是抖動；
      `hand_safety.c` 是夾限不是拒絕，所以誤判會退化成安全姿勢而不是整個失敗。
      這是設計上想要的，但**先在 `--sink=none` 上跑到有把握再接硬體**。

**還沒回答**：37 類要收到幾類；做分類（離散手勢 → 查表）還是回歸（連續 pose）；
臂環戴的位置換一次要不要重訓。

### 2. 部署 KD240

兩段，先後有序。C 那段已經證明，**視覺那段才是工作量**。

#### 2a. C 那一段（已證明，剩收尾）

2026-08-06 在 `.228` 的 `eth1`（PL backed 的 J25 埠）上手動跑過：`ecat_scan` 有回應、
`hand_ctl state`/`scale` 讀到 890..1850、`handd` 進 OPERATIONAL、`hand_api.py open`
真的動。步驟見 [`hand_fw/README.md`](hand_fw/README.md)。

> **板上每一個 EtherCAT 指令都要用 `ubuntu` 跑，不要用 root。**
> `/tmp/inspire_hand.bus.lock` 是 `ubuntu:ubuntu`，root 在這塊板上 `flock()` 不了
> 不屬於它的檔案，而 `hs_lock()` 只會回報籠統的 "BUS BUSY"——跟真的有第二個主站
> 長得一模一樣。

- [ ] 網卡 up 與開機自動化。`.112` 那份是 `ecat-link.service` + `enp17s0`
      （見 [`teleop/README.md`](teleop/README.md)），板上是 `eth1`，要自己一份。
- [ ] `handd` 開機常駐。`hand_fw/systemd/` 有 `install.sh` 與 unit 樣板，**沒在板上跑過**。
- [ ] 在板上重跑一次離線測試組（`make test`、`test_scale.py`、`test_daemon.py`…）。
- [ ] **`.112` 的延遲數字不能直接搬。** 500 Hz、觸發策略、喚醒時間在 aarch64 上
      要重量一次。
- [ ] **逐軸 sweep 還沒做**（`filter/README.md` 的待辦、`hand_fw/hand_safety.h:63-68`
      自己記著的那件事）。現在的行程上下界是「操作者碰巧掃到的極限」，只對 `.112`
      那一台。**上板前應該有這個工具**，否則板上等於拿未量測的限位在跑。

#### 2b. 把 MediaPipe 換成自己的 nn

**為什麼非換不可**（都是量到的，不是嫌它）：

- `setup.sh` 在 aarch64 直接拒絕跑。板子 1.9 GB RAM、無 swap，pip 從原始碼編視覺
  套件會 OOM。
- PyPI 最新的 aarch64 wheel 停在 **mediapipe 0.10.18**，`requirements.txt` 就是為了
  這件事把版本上下界都釘死。現況是「能跑，但踩在一顆沒人往前推的 wheel 上」。
- 板上實測 **35.8 ms 追蹤、四執行緒 27.9 FPS**。

**先決定介面，這是分岔點：**

| 方案 | 輸出 | 權重 | 影響 |
|---|---|---|---|
| **A1 自建管線** | 21 個 landmark | 原廠 `.tflite` | 自寫 anchor decode / NMS / ROI transform。**下游一行不用改** |
| **A2 自訓模型** | 21 個 landmark | 自己訓 | A1 全部加上訓練。**進 PL 的唯一路** |
| **B 端到端** | 六軸目標 | 自己訓 | 校正窗、`thumb_features()`、遮擋判定全部作廢——也就沒有 `thumb_trust()` 那一層擋幻覺 |

**建議先做 A1**，它是部署的解鎖點而且下游不動。選項的完整推導、作廢清單與
理由在 vault 的 `MediaPipe_Replacement_Interface_Fork`（見本節開頭的路徑）。

**走 A2 或 B 就一定要重跑的**：

- `camera/test_mapping.py`（映射與視角無關）。
- **每個人的 `calibration.json` 全部作廢**——窗是 landmark 角度、量自
  `model_complexity=0`，換模型就不是同一把尺了。
- `filter/` 的參數。deadband 是「度 × 該軸增益」，度的定義換了，門檻就換了。

**還沒回答**：模型跑 CPU 還是 KD240 的 DPU/PL？走 DPU 的話要 Vitis-AI 量化流程，
那是另一條供應鏈，不在這個 repo 裡，會影響 2b 的工作量一個數量級。

### 3. 部署前的重構（瘦身）

目的：板上只放**跑得起來需要的東西**。1.9 GB RAM 無 swap，這不是潔癖。

先量到的（tracked 大小／行數，2026-08-10）：

| 目標 | 現況 | 想法 |
|---|---|---|
| `hand_fw/experiments/` | **53 檔、5575 行** | 協定逆向的一次性程式。結論已經在 vault 的 `Execution_Trigger_Settled`。板上完全用不到 |
| `nn/` | **16 檔、2707 行** | 全是一次性 probe（`thumb_steps` / `tilt_test` / `flex_test` / `rot_floor_probe`…），結論在 `nn/README.md`。資料夾名字指的那個模型**還不存在** |
| `libemg/` submodule | 含 `sifi_bridge_windows.exe` | 板上（現階段）不需要 |
| `requirements.txt` | 一份給全部 | matplotlib / pandas / numba / llvmlite 只有**離線分析**要（`run_log` 畫圖、`measure_jitter plot`、`inspect_recording`）。**拆成 runtime / dev 兩份**，板上只裝 runtime |
| `handd.c` | **1712 行，全樹最大** | 這個不是刪的對象，是**要看的對象**——它就是要上板的東西 |

**原則**（免得瘦身把證據一起瘦掉）：

- 一次性 probe 刪掉之前，確認它的**結論**已經寫進 README 或 vault，而且**留下來的
  資料還重跑得出來**。`runs/` 那八份是刻意留的證據，不動（見
  [`runs/README.md`](runs/README.md)）；`filter/` 的 `still.csv`、`moving.csv`、
  `dropout.csv` 同理，出貨的濾波器常數就是從它們算出來的。
- 刪掉的東西 git 還在。README 上留一句「這件事在哪個 commit 做過」比留著檔案有用。
- **測試不瘦。** 離線測試組是唯一能在沒有手的情況下說話的東西。

### 順序

三件事不是平行的，卡點在這裡：

1. **`feat/filter-stage` 合回 `main`**（51 commit）。不然下面每一步都在動同一批檔案。
2. **瘦身**。不需要手、不需要板，隨時可以做。
3. **決定 2b 的介面（A1 / A2 / B）**。⚠️ **走 A2 或 B 就擋著第 1 項的標籤工作**
   ——視覺標籤修好之後又換模型，等於重來一次。A1 則兩者可並行。
4. **逐軸 sweep 工具**（`filter/README.md`）。上板前該有。
5. **上板**（2a 收尾 + 2b）。
6. **EMG 整合**。標籤問題解決之後可以跟 5 平行——它們共用同一條下游，不搶同一份硬體。
