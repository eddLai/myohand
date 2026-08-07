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

## Quickstart

**在 .112（`ssh ntk112`，手接在那台）上跑 teleop**，checkout 在
`~/myohand-feat-filter-stage`：

    cd ~/myohand-feat-filter-stage/teleop
    DISPLAY=:1 ./run_teleop.sh --device 0        # handd 沒在跑就加 --iface=eno1

`run_teleop.sh` 自己找直譯器、自己接上已經在跑的 `handd`（沒有就起一個，
結束時收掉）。視窗開了之後**要按 `A` 或點 SYNC 才會開始跟隨**，`f` 切換
濾波器開關來當場對照，`q` 結束並印出這次的 summary。細節見
[`filter/README.md`](filter/README.md)。

視窗上還有哪些按鈕、校正怎麼跑、卡住了怎麼辦 —— 見
[`teleop/README.md`](teleop/README.md)。第一次校正前先看
[`nn/CALIBRATION.md`](nn/CALIBRATION.md)。

每跑一次就在 `runs/<時間戳>/` 留下 `frames.csv`、`meta.json` 和
`summary.txt`；`runs/` 已 gitignore。

**第一次架這台機器**（或換一台）：

    ./setup.sh                                    # venv + SOEM + C binaries + caps（sudo 一次）
    ECAT_IFACE=eno1 ./hand_fw/hand_ctl state      # 遙測，不會動；先確認手在線上

網卡名每台不一樣（`.112` 是 `eno1`，`.28` 是 `eno1`，KD240 依線接在哪是
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
