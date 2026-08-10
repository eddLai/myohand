# runs — 留下來的 teleop 紀錄

每次跑 teleop 都會在這裡開一個 `<時間戳>/`。**大部分是雜訊，所以預設不進 git**
（`.gitignore` 的 `runs/*`）。

**要留下來的就給它一個描述性的名字**——資料夾名帶底線後綴的會被追蹤：

    mv runs/2026-08-07T20-23-45 runs/2026-08-07T20-23-45_fast_open_and_close

每個目錄裡：

| 檔案 | 內容 |
|---|---|
| `frames.csv` | 每一幀。**這是真相來源**，raw 目標、實際送出、ANGLEACT、電流、STA、增益戳記 |
| `meta.json` | 濾波參數、校正 profile、git commit、sink |
| `summary.txt` | 人看的那份 |
| `plot.svg` | 圖。衍生物，可以從 `frames.csv` 重生 |

重生 summary 或圖：

    cd filter
    python3 run_log.py ../runs/<名字>                    # 重寫 summary.txt
    # 產圖的指令就印在 summary.txt 最後

格式與各欄意義見 [`../filter/README.md`](../filter/README.md)；逐欄語意與那些陷阱在
vault 的 `Command_Filter/Run_Log_Reference`，量測方法與參數依據在同一層的
`Command_Filter_Reference`。

## 目前留著的

| Run | 內容 | commit |
|---|---|---|
| `2026-08-07T20-23-45_fast_open_and_close` | 快速張握拳，**不等**機器手到位 | 修正前 |
| `2026-08-07T20-25-20_slow_open_and_close` | 等機器手到位了才做下一個動作 | 修正前 |
| `2026-08-07T20-49-07_open_close` | 同樣的張握拳 | `a5c01a6` 之後 |
| `2026-08-07T20-50-36_hold` | 握住不動 | `a5c01a6` 之後 |

前兩份是同一位操作者的快/慢對照，也是找出 thumb_bend 被驅動超過機械行程的
證據。後兩份是 `a5c01a6`（把 thumb_bend 映射到它真正的行程）之後的驗證。

| | 修正前（快） | 修正前（慢） | 修正後 open_close | 修正後 hold |
|---|---|---|---|---|
| thumb_bend absorbed | 82% | 64% | **24%** | **4%** |
| thumb_bend 峰值電流 | **1397 mA** | 84 mA | 95 mA | 64 mA |
| 任一軸 >200 mA 的幀 | **22** | 0 | **0** | **0** |
| thumb_bend ANGLEACT 下緣 | **1123**（停止點） | 1129 | **1143** | 1177 |
| thumb_bend STA | （當時沒記） | （當時沒記） | **0,1** | **0,1** |

STA 只出現 0 和 1，**沒有 5 或 6**——那是 `hand_safety.c` 的堵轉碼。手自己說它
沒有再頂到停止點。
