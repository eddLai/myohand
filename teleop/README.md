# teleop — 用手勢遙控 RH56F1

開一個視窗，相機看你的手，機器手跟著動。

---

## 開起來

```bash
cd ~/myohand/teleop && ./run_teleop.sh --iface=enp17s0
```

`--iface` 是**接機器手那張網卡**。給了它就會自己啟動 `handd`、跑完自己收掉；
Ctrl+C 也會收，不會留東西卡在 EtherCAT 匯流排上。

⚠️ **網卡名每台機器不一樣。`ntk112` 上是 `enp17s0`。**

**先把網卡拉起來再問它有沒有線**——這是唯一重要的一步：

```bash
sudo ip link set enp17s0 up          # 沒這行，下面永遠回 no
sleep 2
for i in $(ls /sys/class/net | grep -v -e lo -e docker); do
    printf '%-10s %s\n' "$i" "$(ethtool $i 2>/dev/null | grep 'Link detected')"
done
```

> ⚠️ **關著的網卡跟空的網卡長得一模一樣。** admin down 的介面 PHY 沒通電，不會協商，
> 所以不管插什麼 `ethtool` 都回 `Link detected: no`、`carrier` 檔讀出 `Invalid argument`、
> **孔上的燈也不會亮**。用看的、用讀的都分辨不出來。
>
> 本檔 2026-08-10 之前寫的是「`carrier` 是 `1` 的那張才是，`ntk112` 上是 `eno1`」——
> **那條規則在這台永遠指向辦公網卡**：`eno1` 持有 `120.126.83.112`、是預設路由、也是
> ssh 進來的那條，它的 carrier 當然一直是 1；而手所在的 `enp17s0` 平常是關的，
> 於是被判成「另一台機器的名字」。那次誤判花了一個下午。

`enp17s0` 是 NetworkManager `unmanaged`（對 EtherCAT 是對的，不該讓它去 DHCP），
所以**每次重開機都會回到 down**。要它自己起來：

```bash
sudo tee /etc/systemd/system/ecat-link.service >/dev/null <<'EOF'
[Unit]
Description=Bring the EtherCAT NIC up (no IP, NM leaves it unmanaged)
After=network-pre.target
[Service]
Type=oneshot
ExecStart=/sbin/ip link set enp17s0 up
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now ecat-link.service
```

**證據**（2026-08-10）：`ip link set enp17s0 up` 之後 `Link detected: yes`，
`./run_teleop.sh --iface=enp17s0` 進到 `handd: OPERATIONAL` 並錄到真的 ANGLEACT
與電流（`runs/2026-08-10T15-31-58_ecat_enp17s0`）。

### 沒有手也想看

```bash
./run_teleop.sh --sink=none
```

相機、辨識、六根目標條、校正全都能用，只是不送給任何硬體。改 UI 或看數值時用這個。

### daemon 已經在跑的話

不要給 `--iface`：

```bash
./run_teleop.sh
```

它會連上 `/tmp/inspire_hand.sock` 上現有的 `handd` 並且**留著不關**——別人正在
用的東西不該被你關掉。

---

## 視窗上有什麼

| | 做什麼 | 鍵盤 |
|---|---|---|
| **SYNC ON / OFF** | 開了才會把你的手勢送出去 | `a` |
| **CALIBRATE** | 量你的手（見下） | `c` |
| **OPEN HAND** | 六軸全開，把手張平 | |
| **SETTINGS** | 握力 / 速度 / 相機編號 / 平滑 | |
| | 送一次目前姿勢（SYNC 關著也能用） | `空白鍵` |
| | 離開 | `q` 或 `ESC` |

右側是六根目標條（pinky / ring / middle / index / thumbBend / thumbRot），
條滿＝張開、條空＝閉合。**條變紅色代表撞到端點**——常態性撞到就是校正窗跟你的手
對不準，該重新校正。

`SETTINGS` 裡的四個旋鈕會存進 `teleop_settings.json`，下次開還在。

---

## 校正：按 CALIBRATE 就好

按下去之後這些事會自己發生，你只要照視窗上的中文擺姿勢：

```
1. 手先張開          那一分鐘沒人在驅動它，不該讓它一直抓著東西
2. 相機交給校正視窗
3. 六個姿勢          每個準備 4 秒、定住 5 秒；邊框變綠＝正在錄
4. 視窗自己關
5. 存檔              寫成 calibration.json 裡的新一筆，舊的不動
6. 相機回來
7. 濾波器重建        用新校正的增益，並且換一份新的 run log
8. SYNC 自動開       手立刻開始跟著你動
```

**存檔失敗就不會開 SYNC。** 什麼都沒量到的時候不該讓機器手自己動起來。

**第 7 步會讓這次 session 留下兩份 run log**，校正前一份、校正後一份，各自帶自己的
增益。要看的通常是後面那份；前面那份留著，因為讓你決定重新校正的往往就是它。
理由見 [`../filter/README.md`](../filter/README.md) 的「一份 run log ＝ 一段連續的濾波器」。

姿勢的細節、為什麼「拇指捲曲」不是「把拇指壓在手掌上」、以及校正修不好的那些事，
見 [`../nn/CALIBRATION.md`](../nn/CALIBRATION.md)。**第一次校正前先看那份。**

### 每個人一份窗

`camera/calibration.json` 在 `.gitignore` 裡，**不會跟著 clone 下來**。
新機器第一次跑會用程式內建預設值（那是別人的手，不準），按一次 CALIBRATE 就有自己的。

同一台機器多人共用時，`active` 只有一個欄位，**誰最後校正誰就搶到**。
自己的窗自己指名：

```bash
./run_teleop.sh --iface=enp17s0 --profile=你的名字-20260807
```

有哪些窗可以選：

```bash
cd ~/myohand && venv/bin/python3 camera/hand_mapping.py
```

---

## 參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `--iface=NAME` | — | 給了就自己起 `handd`；不給就連現有的 |
| `--device N` | 設定檔裡的值 | 相機編號。也可以在 SETTINGS 裡改 |
| `--profile NAME` | 檔案裡的 `active` | 用哪一筆校正窗 |
| `--sink=daemon\|hand_set\|none` | `daemon` | 目標送去哪。`none` ＝ 不接硬體 |
| `--rate N` | 50 | 串進 daemon 的頻率（Hz） |
| `--width` / `--height` | 960 / 540 | 擷取解析度 |
| `--headless` | 關 | 沒有視窗，自動開 SYNC。給端對端測試用 |

一個不以 `-` 開頭的位置參數會被當成相機編號：`./run_teleop.sh 0`。

---

## 卡住的時候

**`could not open the daemon sink`**
`handd` 沒在跑。加 `--iface=enp17s0` 讓它自己起，或另開一個終端機跑
`cd ~/myohand/hand_fw && ./handd --iface=enp17s0 --socket=/tmp/inspire_hand.sock`。

**`../hand_fw/handd is not built`**
在 worktree 或新 clone 裡跑，執行檔還沒編：`make -C ../hand_fw all && sudo make -C ../hand_fw cap`。
**新 clone 還要先建 SOEM**（gitignore 的 vendor clone，`pull` 永遠不會帶它來）——
`make` 會自己把步驟印出來，照著做。要 cmake ≥ 3.28，系統那顆通常太舊，用 venv 裡的。
細節見 [`../hand_fw/README.md`](../hand_fw/README.md) 的「建置」。

**相機開不起來 / 畫面是黑的**
先確認沒有別的程式佔著：

```bash
ls /dev/video*
for d in /dev/video*; do h=$(fuser "$d" 2>/dev/null); [ -n "$h" ] && echo "$d:$h"; done
```

沒人佔的話就是編號不對，在 SETTINGS 裡把 `Camera` 改成別的數字（0..5）。

**在 worktree 裡跑，校正窗不見了**
`camera/calibration.json` 和 `teleop/teleop_settings.json` 都在 `.gitignore` 裡，
新 checkout 一個都沒有。連過去：

```bash
ln -s ~/myohand/camera/calibration.json    camera/calibration.json
ln -s ~/myohand/teleop/teleop_settings.json teleop/teleop_settings.json
```

用 symlink 不要用複製——一台機器一份窗，兩份會各自漂移。

**`qt.qpa.xcb: could not connect to display`**
從 SSH 進來、沒有 X。腳本預設 `DISPLAY=:0`，但桌面不一定在 `:0`
（`ntk112` 的在 `:1`）。要嘛在機器自己的終端機跑，要嘛 `DISPLAY=:1 ./run_teleop.sh ...`。

**`need CAP_NET_RAW or root`**
這句話對兩件事都會出現，**兩個都要查**：

1. **capabilities 真的沒設**（`getcap ../hand_fw/handd` 是空的）→ `sudo make -C ../hand_fw cap`。
   **每個 worktree、每次重編都要重下**，因為 caps 掛在 inode 上。
2. **網卡名給錯**，包含「名字對但介面是關的」——回去看最上面那段。

分辨方法：設好 caps 之後訊息會**改變**。還是同一句就是 caps；變成
`no EtherCAT slave answered` 就換去查線、電源與介面狀態。2026-08-10 兩種都遇到了，
順序就是這樣。

---

## 這裡有什麼

| 檔案 | 做什麼 |
|---|---|
| `run_teleop.sh` | 啟動腳本。找直譯器、起/收 `handd`、把參數轉給下面那個 |
| `teleop_app.py` | 主迴圈：讀相機 → 算目標 → EMA 平滑 → 送出 |
| `teleop_ui.py` | 疊在畫面上的儀表與按鈕 |
| `preview_ui.py` | 拿靜態圖預覽 UI 版面，不必開相機 |
| `test_calbtn.py` | CALIBRATE 按鈕四種結果的測試（不需相機與硬體） |
| `teleop_settings.json` | 握力 / 速度 / 相機 / 平滑。**不進版控** |

角度怎麼算出來的見 [`../camera/`](../camera/)，目標送出去之後的事見
[`../hand_fw/README.md`](../hand_fw/README.md)。
