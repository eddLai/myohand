# runs — 留下來的 teleop 紀錄

每次跑 teleop 都會在這裡開一個 `<時間戳>/`。**大部分是雜訊，所以預設不進 git**
（`.gitignore` 的 `runs/*`）。

**一次 teleop 可能開不只一個**：按 CALIBRATE 或換相機會把濾波器重建，紀錄就切在
那裡——一份等於一段連續的濾波器，這樣它才驗得了自己。同一秒切兩次的話後面那個
會是 `<時間戳>-2`。**動 Deadband 滑桿則不切**——它只換門檻、不清狀態，所以逐幀
記進 `deg` 那一欄，重放照著走。為什麼見 [`../filter/README.md`](../filter/README.md)。

**要留下來的就給它一個描述性的名字**——資料夾名帶底線後綴的會被追蹤：

    mv runs/2026-08-07T20-23-45 runs/2026-08-07T20-23-45_fast_open_and_close

每個目錄裡：

| 檔案 | 內容 |
|---|---|
| `frames.csv` | 每一幀，50 欄。**這是真相來源**，raw 目標、實際送出、ANGLEACT、電流、STA、增益戳記、當幀的 `deg` |
| `meta.json` | 濾波參數、校正 profile、git commit、sink |
| `summary.txt` | 人看的那份 |
| `plot.svg` | 圖。衍生物，可以從 `frames.csv` 重生 |

> `meta.json` 的 commit 是**錄的當下**那個 hash，而這條 branch 被 rebase 過兩次。
> 底下幾份記的 `a5c01a6`、`a354f3f`、`5de84f7`、`4c33240` 因此不在 `git log` 裡，
> 它們掛在 `archive/pre-rebase-2026-08-07` 與 `archive/pre-rebase-2026-08-10` 兩個
> tag 底下（都在 origin 上），`git show <hash>` 照樣打得開。

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
| `2026-08-07T20-58-34_open_close` | 同樣的張握拳，30 FPS | `5de84f7` |
| `2026-08-10T15-15-22_before_calibrate` | 張握拳，14.9 FPS | `4c33240` |
| `2026-08-10T15-17-05_after_calibrate` | 張握拳，15.0 FPS | `4c33240` |
| `2026-08-10T15-31-58_ecat_enp17s0` | 張握拳，30 FPS。`enp17s0` 上第一次進到 `OPERATIONAL` | `4c33240` |

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

### 後面四份

`20-58-34_open_close` 是 08-07 那批的最後一份，30 FPS、thumb_rot 增益 4.59。
另外三份是 08-10 在 `.112` 上跑的。四份的重放檢查都是全等（696/696、704/704、
651/651、1119/1119 幀，最差 0.0 counts），也沒有任何一軸碰到 200 mA。

| | `20-58-34` | `15-15-22` | `15-17-05` | `15-31-58` |
|---|---|---|---|---|
| FPS | 30.0 | 14.9 | 15.0 | 30.0 |
| 濾波器吸收（thumb_bend） | 88% | 78% | 65% | 83% |
| 濾波器吸收（四指） | 52–68% | 44–58% | 29–39% | 54–62% |
| 最大 absorbed | **27%**（ring） | 9%（thumb_rot） | 6%（thumb_bend） | 18%（thumb_bend） |
| 峰值電流 | 141 mA | 179 mA | 145 mA | 143 mA |

`20-58-34` 的 absorbed 明顯高一截（四指 18–27%，其餘三份都在 8% 以內）。它和
`15-31-58` 同樣是 30 FPS，後者四指只有 5–8%，所以幀率並不足以解釋這個差距，
目前只是記在這裡，沒有被歸因。

`15-31-58_ecat_enp17s0` 是 `enp17s0` 換線之後第一次進到 `handd: OPERATIONAL`
並錄到真的 ANGLEACT 與電流的一次，`teleop/README.md` 的網路介面那節引它當證據。
1119 幀全部有手。它的 `device` 記的是 `0`，與 argv 一致——底下那個 `device` 記成
4 的問題在這一份沒有出現。

**兩份 08-10 的檔名不成立：那次校正沒有存成。** 名字說它們夾著一次校正，但兩份的
`calibration` 都是 `session-20260807-180953`，六軸增益也逐位元相同
（thumb_rot 都是 `7.06401766004415`）。`meta.json` 沒有記錯——`.112` 的
`camera/calibration.json` 裡只有 `session-20260807-180953` 一個 profile，檔案本身
的 mtime 是 08-10 14:36，早於這兩次 run。那天沒有任何新校正落地。

原因在 `teleop_app.py`：按下 CALIBRATE 之後，`cut_run()` 是**無條件**被呼叫的，
而 `run_calibration()` 只回傳相機，成敗留在 `cal_note` 這個 UI 字串裡從未傳出去。
於是一次被拒絕的校正照樣切出一份新紀錄，前後兩份的 profile 與增益完全相同，
記錄上無從分辨。切紀錄這件事本身是對的（相機離開了一分鐘、濾波器被重建），
**缺的是把結果記下來**。現在新紀錄會帶一行 `follows`：

    follows       calibration refused - profile unchanged
    follows       calibration -> session-20260810-...

這兩份是在那之前錄的，所以沒有那一行。**它們是兩段獨立的張握拳，不是校正前後的
對照。**

（另外，`summary.txt` 末尾那行畫圖指令指的是改名前的路徑。照上面的慣例改名之後
它就過期了，要畫圖的話把路徑換成現在的目錄名。）
