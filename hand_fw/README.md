# hand_fw — RH56F1 EtherCAT 控制堆疊

Inspire RH56F1 靈巧手走 EtherCAT 的控制堆疊。這份 README 只講**怎麼跑**；
推導、量測數字與設計理由在 ExoPulse_docs vault 的
`Inspire_RH56F1/01_Hand_Control/EtherCAT/Hand_FW_Reference`，協定逆向的
結論檔是同目錄的 `Execution_Trigger_Settled`。

已在三台主機上驅動過，網卡名每台都不同，所以樹裡沒有一處寫死網卡：
**`$ECAT_IFACE` 選網卡，預設 `eth0`**，`experiments/ecat_scan` 告訴你哪一張會回應。

| 進入點 | 用途 |
|---|---|
| `handd` | 常駐主站。握住 OPERATIONAL、跑 PDO 迴圈、內含安全層、開 unix socket |
| `hand_ctl` | C 核心，單次：喚醒 → 送姿勢 → 離開，印 JSON 遙測。約 1.9 秒 |
| `soem_build/hand_set` | 精簡版單次姿勢設定器，約 2–3 秒。daemon 之前的路徑 |
| `hand_client.py` | daemon 的 Python client |
| `hand_api.py` | Python 庫 + CLI，手勢 open / fist / middle / point / release |
| `hand_server.py` | HTTP JSON API，只綁 `127.0.0.1:8100` |
| `../teleop/run_teleop.sh` | webcam 手勢鏡射，含 SYNC 按鈕 |

其餘檔案（`hand_sink.py`、`hand_scale.py`、`hand_latency.py`、`systemd/`、
`experiments/`、`geometry/`）的角色見 vault 的 `Hand_FW_Reference` 第一節。

## 快速上手

    ECAT_IFACE=enp17s0 ./hand_ctl state    # 遙測，不會動
    ./hand_ctl scale                       # 目標數字的意義，不碰匯流排
    ./handd --iface=enp17s0 &              # 常駐主站
    python3 hand_client.py state           # 跟它講話
    python3 hand_api.py open               # CLI 手勢（單次路徑）
    ../teleop/run_teleop.sh                # webcam teleop 視窗

`hand_ctl` 與 `hand_set` 從 **`$ECAT_IFACE`** 讀網卡名，預設 `eth0`；
`handd` 用 **`--iface=`**。在網卡不叫 `eth0` 的機器上省略它，錯誤訊息會是
`ecx_init (need CAP_NET_RAW or root)`——看起來像權限問題，其實是網卡名。

## 常駐 daemon

    ./handd --iface=enp17s0                     # continuous，500 Hz（預設）
    ./handd --iface=enp17s0 --trigger=watchdog  # 備援：靠靜默觸發套用
    ./handd --simulate                          # 不碰匯流排，開發 client 用

一個行程握住 OPERATIONAL，而不是每個姿勢各付一次連線代價；安全層住在它裡面，
所以 teleop、EMG 分類器、HTTP server 和臨時腳本都走同一個 socket，誰都繞不過 guard。

**`--rate-hz` 預設 500，不要調到 1000。** 1 kHz 是這隻手唯一什麼都不套用的速率
（從站應用層跑不完一個週期）。四種 `--trigger` 策略的比較表與這個結論的完整證據，
見 vault 的 `Execution_Trigger_Settled`。

`handd --explain-al=0x002d` 把 AL 狀態碼讀成人話。

## 跑整條鏈

    # 一個 terminal：起 handd、跑 teleop，teleop 結束時把 daemon 一起收掉
    ../teleop/run_teleop.sh --iface=enp17s0 --device=0

或拆開跑，當你要讓 daemon 活得比視窗久：

    ./handd --iface=enp17s0 &            # 1. daemon 握住匯流排
    python3 verify_following.py          # 2. 先證明控制端，再把相機加進來
    ../teleop/run_teleop.sh --device=0   # 3. 視覺端；看到 daemon 就沿用，不收掉它

視窗開起來後按 **SYNC** 才會開始送；在那之前什麼都不會送出，狀態列會這樣說
（"Ready - press space to send this pose"）。**OPEN HAND** 不管有沒有 SYNC 都會送，
所以它是最快證明整條鏈活著的方式。

### 在 ntk112（`.112`）上用 conda 跑

這台的 teleop 相依裝在 conda env 而不是 `venv`，而且啟動器有三個預設值在這裡是錯的。
2026-08-06 實測：`handd` 進 OPERATIONAL、把待機（STA=7）軸在 201 ms 內喚醒，
teleop 跑 684 畫格 / 23.0 秒 = **29.8 FPS**（960×540），一次中斷把 teleop 和 daemon
依序收掉，沒有殘留的 socket 或行程。

    conda activate myohand-teleop
    cd <checkout>/teleop
    TELEOP_PYTHON=$HOME/miniconda3/envs/myohand-teleop/bin/python \
      DISPLAY=:1 ./run_teleop.sh --iface=enp17s0 --device 0

**`TELEOP_PYTHON` 在獨立 worktree 裡不是選配。** `pick_python` 會依序試
`../venv` 和 `$HOME/myohand/venv`，而後者在這台存在、屬於**另一個 checkout**、
而且 `cv2` 與 `mediapipe` 都 import 得過——於是檢查通過，teleop 就這樣安靜地跑在
別人的直譯器上。先 `conda activate` 沒有用：這支腳本不看 `$PATH`。

**`DISPLAY=:1`。** 這台的圖形 session 是 `:1`，腳本預設 `:0`，而 `:0` 不存在
（`xdpyinfo` 會回 "unable to open display"）。`XAUTHORITY` 不用管，腳本會自己找到
`~/.Xauthority`。

**`--iface=enp17s0`。** 沒給它、也沒 export `$ECAT_IFACE` 的話，`handd` 會退回
`eth0`，這台沒有。手也是六張網卡裡唯一有 carrier 的，所以
`cat /sys/class/net/enp17s0/carrier` 讀到 0 就是線或手的電源斷了——`handd` 會啟動、
報 "no EtherCAT slave answered"、然後退出。先確認這個，再懷疑軟體。

每個 worktree 要自己設 capabilities，因為 caps 掛在 binary 上，而每個 worktree
各自編譯：

    make -C hand_fw all && make -C hand_fw cap    # cap 需要 sudo 一次

`handd` 是最容易漏的一個：它比其他兩支多要 `cap_sys_nice` 與 `cap_ipc_lock`，
而沒有 `cap_net_raw` 就根本開不了網卡。相機是 `--device 0`（`/dev/video1` 是同一顆
sensor 的 metadata node）。

### 在 KD240（`.228`）上的手動冒煙測試

2026-08-06 對 `eth1` 驗證（PL backed 的 J25 埠；`eth0` 是板子的一般網路上行，
手從來不在那條上）。

> **每一個 EtherCAT／`hand_ctl`／`handd` 指令都要用 `ubuntu` 跑，不要用 root。**
> `/tmp/inspire_hand.bus.lock` 是 `ubuntu:ubuntu 0664`，而這塊板子上 root 無法
> `flock()` 一個不屬於它的檔案（即使開得起來）——`hs_lock()` 只會回報籠統的
> "BUS BUSY"，跟真的有第二個主站長得一模一樣，而不是權限錯誤。

    ip -br link show eth1                                  # 要看到 LOWER_UP，否則檢查 J25 線
    sudo -u ubuntu ./experiments/ecat_scan eth1            # 唯讀：確認從站會回應
    sudo -u ubuntu env ECAT_IFACE=eth1 ./hand_ctl state    # 唯讀遙測
    sudo -u ubuntu env ECAT_IFACE=eth1 ./hand_ctl scale    # 確認 C 與標頭一致：890..1850

    # 起 daemon：會喚醒 STA=7 的軸（原地小幅擺動）並握住 OPERATIONAL，
    # 但在有人要求姿勢之前不送任何姿勢
    sudo -u ubuntu env ECAT_IFACE=eth1 ./handd --iface=eth1 &
    sudo -u ubuntu python3 hand_client.py state            # bus:up、applying:true、sta 全 2

    sudo -u ubuntu python3 hand_api.py open                # 真正的動作測試，走 handd
    sudo -u ubuntu pkill -TERM handd                       # 收工

板子上**不要跑 `setup.sh`**（它自己會在 aarch64 上拒絕）：1.9 GB RAM 沒有 swap，
pip 從原始碼編視覺套件會 OOM。那裡只建 C 端：

    export PATH="$HOME/rh56f1_kd240/ethercat/buildenv/bin:$PATH"   # cmake >= 3.28
    make all && make cap

## 中斷與收尾

Ctrl+C 在每個進入點都會釋放它握著的東西，`test_release.py` 會真的啟動每一個、
中斷它，再去問核心資源有沒有回來。檔案描述元（相機、socket、`flock`）行程一死
核心就回收；需要幫忙的是**行程真的會死**，以及核心不會替你收拾的兩件事：
它生出來的子行程，還有停在動作中途的手。

| 進入點 | 中斷時 |
|---|---|
| `handd` | SIGINT/SIGTERM/SIGHUP；unlink socket、放掉匯流排鎖、手停在原地 |
| `../teleop/teleop_app.py` | 訊號設旗標給迴圈檢查；相機與 sink 在 `finally` 釋放 |
| `../teleop/run_teleop.sh` | 單一 EXIT trap 先停 teleop 再停 daemon，但只停它自己起的那個 |
| `hand_server.py` | 讓進行中的請求跑完再關，並釋放手 |
| `HandSetSink` | 停掉它生出來的 `hand_set`——孤兒會一直握著匯流排鎖 |
| `InspireHand` | 可以 `with InspireHand() as hand:` 使用 |

### 前景、後景，以及 Ctrl+C 打得到誰

Ctrl+C 只把 SIGINT 送給 terminal 的**前景 process group**，所以 `handd` 怎麼起的，
決定了它怎麼收：

| 起法 | prompt 會回來 | Ctrl+C 打得到 | 關 terminal 會死 |
|---|---|---|---|
| `./handd --iface=enp17s0` | 否 | 是 | 是 |
| `./handd --iface=enp17s0 &` | 是 | **否** | 是（SIGHUP） |
| `setsid ... ./handd ... &` | 是 | **否** | **否** |

訊號一旦送到，三種起法的收尾完全一樣（SIGINT/SIGTERM/SIGHUP 共用同一個 handler）；
差別只在哪些訊號送得到。用 `&` 丟後景，daemon 就不在前景 group，Ctrl+C 碰不到它，
只能 `pkill -TERM handd` 或關掉 terminal。用 `setsid` 則連 controlling terminal
都沒有，`pkill -TERM handd` 是唯一的路——那是「要讓 daemon 活過起它的 ssh session」
時的正確形式，互動操作時則是錯的。

互動時就用前景、開自己的 terminal：log 在眼前，Ctrl+C 就夠。**`Ctrl+Z` 不是離開**
——SIGTSTP 會把行程停住，等於在 daemon 還握著匯流排鎖的情況下停掉 EtherCAT 週期；
要用就後面補一個 `bg`，否則別用。

`handd` 與 `hand_client` 都讀 `$HAND_SOCKET`，export 它會讓這一對一起搬家。

> **絕不要在 `handd` 握著匯流排時跑第二個主站。** `ecat_scan` 看起來唯讀其實不是，
> 它的 `config_init` 會驅動從站狀態機；在跑著的 daemon 底下這樣做，會讓從站繼續
> 接受目標卻一個都不套用，而所有指示燈都還說一切正常（`bus up`、`al=0`、每個回覆
> `ok`、`seq` 增加、遙測更新），就是沒有動作也沒有電流。現在 `ecat_scan` 會去拿同
> 一把匯流排鎖並寧可拒絕，daemon 也會計數「既無位移也無電流」的步，連續三次就出聲
> 並以離開碼 5 結束（`--on-stuck=report` 可改成留著不退）。要在不製造第二個主站的
> 情況下讀狀態，就問 daemon：`hand_client.HandClient().state()`。

## 安全層

每個 binary 的目標在抵達 PDO 之前都會穿過共用的 `hand_safety.c`，所以沒有任何
呼叫端——Python API、HTTP server、teleop 或臨時腳本——能下出卡死機構的姿勢：

- **關節互鎖**：同時閉合食指與拇指會把拇指抬開（它們機械性相撞並觸發 STA=5）；
  食指蜷曲時拒絕拇指往掌心旋轉。標成不變（`-1`）的軸依即時 ANGLEACT 判斷。
- **堵轉退避**：處於 STA 5/6 或吃電超過 400 mA 的軸，會先往開的方向退。
- **逐軸 profile**：thumb_bend 有自己的力量上限與較低速度。
- **幾何表**：`hand_collision_table.h`（由原廠 STEP 產生，**不要手改**）額外擋住
  純量規則漏掉的半蜷曲低旋轉口袋。
- **匯流排鎖**：`flock` 串行化主站——同一張網卡上兩個主站會讓從站拒絕進 OPERATIONAL。
- **範圍夾限** 890..1850、`force<=1000`（預設 500）、`speed 50..1000`。

Guard 一律**夾限而非拒絕**，所以串流式的 teleop 來源會退化成安全姿勢而不是整個
失敗；`hand_ctl` 在 `guarded` / `guard_note` 回報它改了什麼。各條規則的推導、
幾何表的再生成流程見 vault 的 `Hand_FW_Reference`。

### 目標尺度

軸序 `[pinky, ring, middle, index, thumb_bend, thumb_rot]`，目標 `-1` = 該軸不變。
其餘**不是** `0..2000`：**目標就是 ANGLEACT counts，一對一，約 `890` 全閉到
`1850` 全開**。低於約 `890` 是往閉合止點頂，不是去那個數字。

尺度只定義一次，在 `hand_safety.h`；Python 端在 `hand_scale.py` 鏡射並對
`hand_ctl scale` 自我驗證。逐軸行程並不相同（thumb_bend 靜止在 `~1375`、
thumb_rot 在 `~1048`），對這兩軸下 `1850` 是把它們頂在止點上。

## 觸覺（T1 版）

T1 版的手把觸覺放在 TxPDO 軸狀態後面：**34 個 short = 8 個電容模組
（4 指尖 + 拇指尖 + 掌心 3 區）× 4 個量（法向力/切向力/切向方向/接近覺）
+ 2 欄未定**。`handd` 的 `state` 與 `hand_ctl state` 都回 `"tac"`：T1 是
34 個原始值，非 T1 的手輸入影像更短、探不到這塊，回 `null`。

**逐欄順序尚未實機標定**——datasheet 只說有哪 8 個模組和哪 4 個量，沒說
線上順序。標定用：

    ./tac_view.py        # 對著 daemon 開 live 表格，逐一按壓模組看哪幾欄動

表格的 8×4 排列是「模組優先」假設，標定完成前只信平面索引、別信行列標籤。
單位同樣未定（量程 30 N、精度 5%FS，出自選型手冊 V19 p.10）。

## 離線測試（都不需要硬體）

    make test && ./test_safety        # 22 項互鎖、尺度與幾何檢查
    python3 test_scale.py             # C 與 Python 對尺度的看法一致
    python3 test_daemon.py            # daemon，對模擬從站
    python3 test_teleop_sink.py       # 串流 client 路徑
    python3 test_calibration.py       # 校正 profile 不會被蓋掉
    python3 test_api_compat.py        # 兩條路徑呈現同一套 API
    python3 test_release.py           # 中斷任何東西都會釋放它握著的資源
    python3 ../camera/test_mapping.py # mapping 與視角無關
    python3 ../pid/test_pid.py        # 閉環修正，對 plant stub

## 建置

    ../setup.sh    # repo 根目錄一次搞定：venv、SOEM、cmake、make、cap（KD240 除外，見上）

或逐步來（從 repo 根目錄）：

    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    git clone https://github.com/OpenEtherCATsociety/SOEM.git hand_fw/soem_build/SOEM
    cmake -S hand_fw/soem_build/SOEM -B hand_fw/soem_build/build
    cmake --build hand_fw/soem_build/build -j4
    make -C hand_fw all && make -C hand_fw cap

`cap` 每次重編都要重跑（sudo 一次）。`hand_set.c` 靠 `-I .` 找到 `hand_safety.h`，
所以一律用 `make -C hand_fw` 建置。

## 兩條路徑，一套 API

`hand_api.InspireHand` 在 daemon 起著時走 `handd`，沒起時自己生 `hand_ctl`。
方法名、參數與回傳的 dict 兩邊完全相同，所以 import 它的模組跨越這個切換不用改
任何一行——這是刻意的，`test_api_compat.py` 釘住它。

    hand = InspireHand()          # 自己選路徑；hand.via 說走的是哪條
    hand.pose([...], force=500, speed=800)
    hand.open_hand(); hand.fist(); hand.point()

差別只在成本：daemon 路徑一個姿勢是幾毫秒，`settle=True` 會等軸真的停下來而不是
固定睡一段；`hand_ctl` 路徑則是每次重新連線／喚醒／寫入，約 1.9 秒，大部分花在
列舉與軸自己的行程上。如果 daemon 中途死掉，client 會從那一次呼叫起（以及之後每
一次）退回 `hand_ctl`，而不是把例外丟給呼叫端。

要串流目標、延遲時戳、`dc`、`stats` 就直接用 `hand_client.HandClient`。

## 細節在哪裡

repo 只留操作面。以下都在 ExoPulse_docs vault 的
`Project_Management/Inspire_RH56F1/01_Hand_Control/EtherCAT/`：

| 文件 | 內容 |
|---|---|
| `Hand_FW_Reference` | 控制層完整表、觸發策略比較、延遲八階段與決定性數字、ANGLEACT 尺度推導、安全層各條規則、幾何表再生成、teleop UI 與校正 profile、已知限制 |
| `Execution_Trigger_Settled` | 執行觸發的結論檔：1 kHz 為何不動、六項排除、`0x1C32:12` 證據 |
| `SOEM_Port_Plan` | `.28` 的既有成果、F1 逆向、aarch64 移植與建置陷阱 |
| `Persistent_OP_Probe` | 2026-08-05 前身探測 |

`experiments/results_2026-08-06/` 的原始輸出留在 repo 裡跟產生它的程式放一起。
