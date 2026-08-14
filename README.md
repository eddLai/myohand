> ⚠️ **這個分支已被 `kd240-dpu-20260814` 取代（2026-08-14），可以刪。**
> 它最後一筆加的七個 DPU 腳本，在那個分支上全都有、而且是實際跑出板上數字的
> 版本。這裡記的「自建管線本身」（7.8 → 19.0 FPS）已經併進 `main`。
> 細節見本分支的 `BRANCH.md`。

# myohand

用前臂 EMG 驅動 Inspire RH56F1 靈巧手。Repo 依功能拆開，每個資料夾有自己的 README，細節寫在那裡。

| 目錄 | 內容 | 狀態 |
|---|---|---|
| `emg/` | Myo 臂環擷取，靠 libemg | 可錄資料，還沒有分類器 |
| `camera/` | MediaPipe 手部追蹤 -> 關節角度分解 (`hand_mapping.py`)、校正、mapping 測試 | 可動 |
| `teleop/` | Webcam teleop app：儀表板 UI、SYNC/CALIBRATE/HAND 控制 | 可動 |
| `hand_fw/` | RH56F1 的 EtherCAT 控制堆疊：C core (SOEM)、安全層、Python API + HTTP server + 常駐 daemon。操作說明在它的 README，技術細節在 vault | 可動 |
| `nn/` | EMG -> 姿態的網路 (labels 來自 `camera/hand_mapping.thumb_features`) | 規劃中，見其 README |
| `pid/` | 對 ANGLEACT 回授做閉迴路關節控制 | 規劃中，見其 README |
| `data/` | 已錄好的手勢資料集 | 見下 |
| `libemg/` | submodule（LibEMG/libemg） | |

EMG 那半邊還沒有任何程式碼會去呼叫 `hand_fw/`，webcam teleop 也不讀 EMG。
把兩邊接起來需要一個從 EMG 視窗吐出手勢類別的分類器，那部分還不存在（`nn/` 的規劃）。

## Quickstart

    ./setup.sh                                  # root venv + SOEM + C binaries + caps（sudo 一次）
    ECAT_IFACE=eno1 ./hand_fw/hand_ctl state    # 遙測，不會動
    ./teleop/run_teleop.sh --iface=eno1         # webcam teleop 視窗（串進 handd）

⚠️ 網卡名每台機器不一樣，上面寫的是 `ntk112`（`enp17s0` 在那台是 `down`）。
怎麼查、視窗上有哪些按鈕、校正怎麼跑、卡住了怎麼辦 —— 見
[`teleop/README.md`](teleop/README.md)。第一次校正前先看
[`nn/CALIBRATION.md`](nn/CALIBRATION.md)。

`hand_ctl` / `hand_set` 的網卡來自 `$ECAT_IFACE`（預設 `eth0`），`handd` 用
`--iface=`。網卡不叫 `eth0` 又忘了給，錯誤訊息會是 `need CAP_NET_RAW or root`，
看起來像權限問題其實是網卡名。

Python 進入點走 root venv，例如 `venv/bin/python3 hand_fw/hand_api.py open`；
`camera/` 底下的獨立腳本要從那個資料夾執行：`cd camera && ../venv/bin/python3 calibrate.py`。

環境在 conda 而不是 root venv 的機器（例如 `ntk112`），要用 `TELEOP_PYTHON`
明講用哪個直譯器：

    TELEOP_PYTHON=$HOME/miniconda3/envs/myohand-teleop/bin/python \
      DISPLAY=:1 ./teleop/run_teleop.sh --iface=enp17s0 --device 0

只 `conda activate` 是不夠的——`run_teleop.sh` 不看 `$PATH`，會依序退到
`../venv` 和 `$HOME/myohand/venv`，而後者可能是別人 checkout 的環境且
import 得過，於是靜靜地跑錯直譯器。該主機的其他細節（`DISPLAY=:1`、
網卡、caps）見 [`hand_fw/README.md`](hand_fw/README.md) 的「跑整條鏈」。

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
