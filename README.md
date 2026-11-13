# uart-gateway

讓 Linux 上的 Codex 經 TCP 與 Windows UART 操作 IPCam Shell，不需要 SSH 登入 IPCam。exec 使用已登入的 Shell，terminal 可手動登入與互動操作。

```text
IPCam ← UART → Windows Server（COM3）← TCP / 既有 SSH Tunnel → Client / Codex
```

僅三個核心檔案：`server.py` 管 UART、命令鎖與 Log；`client.py` 提供 exec、monitor、status、interrupt 與 terminal；`protocol.py` 處理 NDJSON 與 Shell Marker。Windows Server 使用 pyserial，Client 僅使用 Python 標準函式庫，也可在 Windows 執行。

## Windows 最簡啟動

需要 Python 3.10 以上。首次安裝，在專案目錄執行：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[server]"
```

啟動 Server：

```powershell
.\.venv\Scripts\python.exe server.py
```

預設獨占 **COM3、115200、8N1、無 flow control**，TCP 僅監聽 **127.0.0.1:18081**，Shell 換行使用 **CR**。DTR／RTS 設為 false；硬體／驅動在開啟 COM 時仍可能影響控制線。COM 被占用時直接報錯退出。

改用 COM4：

```powershell
.\.venv\Scripts\python.exe server.py --serial COM4
```

一個 Server 只開一個 COM，兩個 COM 的驗證依序進行。`--newline` 可選 `cr`、`lf`、`crlf`。Server 不會自動送出 CR、登入、探測或 Ctrl+C。

另開 PowerShell 執行 Client：

```powershell
.\.venv\Scripts\uart-client.exe status
.\.venv\Scripts\uart-client.exe exec "uname -a"
.\.venv\Scripts\uart-client.exe monitor
.\.venv\Scripts\uart-client.exe interrupt
.\.venv\Scripts\uart-client.exe terminal
```

也可直接使用 `.\.venv\Scripts\python.exe client.py ...`，不需要啟用虛擬環境。

## Linux Client

複製專案後安裝，Client 不需要 pyserial：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/uart-client status
.venv/bin/uart-client exec 'uname -a'
.venv/bin/uart-client monitor
.venv/bin/uart-client terminal
```

不安裝入口也可只複製 `client.py`、`protocol.py`，使用 `python3 client.py ...`。

### 既有 SSH Reverse Tunnel

沿用既有 Tunnel，確保 Linux 的 `127.0.0.1:18081` 轉送至 Windows 的 `127.0.0.1:18081`。若從 Windows 手動建立，參考：

```powershell
ssh -N -o ExitOnForwardFailure=yes -R 127.0.0.1:18081:127.0.0.1:18081 user@linux-host
```

程式不管理 SSH。若 Linux 的本機轉送埠不同，改用：

```bash
uart-client --host 127.0.0.1 --port 18082 status
```

`--host`、`--port` 放在子指令前。TCP 本身沒有認證／加密，僅供受信任的本機與 SSH Tunnel 使用；能連到該埠的程序即可 exec。

## CLI 指令

### exec

```bash
uart-client exec 'uname -a'
uart-client exec 'cd /tmp && pwd'
uart-client exec --cwd /mnt/flash2/patch 'ls'
uart-client exec 'sleep 2; printf done' --timeout 5
uart-client exec 'uname -a' --json
```

Server 使用隨機 128-bit ID 的開始／結束 Marker，結束標記帶 Exit Code。標記在 Shell 原文中拆開組合，TTY Echo 不會誤判為完成；只有完整匹配的標記行才算完成，支援跨 RX 區塊與 CRLF。

前提是 IPCam 已登入可用的 POSIX sh／BusyBox ash。命令在 IPCam 的子 Shell 執行，stdin 為 `/dev/null`，stdout／stderr 合併；`exit 7` 不會登出父 Shell，每次命令的工作目錄與變數不會保留到下次。互動程式或重開機指令可能沒有完成標記。輸出是「標記間觀察到的 UART bytes」，可能混入 Kernel／背景訊息，不能當作獨立的純 stdout。

使用 `--cwd` 明確指定本次 exec 的 **IPCam 工作目錄**：

```powershell
.\.venv\Scripts\uart-client.exe exec --cwd /mnt/flash2/patch "pwd"
.\.venv\Scripts\uart-client.exe exec --cwd /mnt/flash2/patch "ls"
.\.venv\Scripts\uart-client.exe exec --cwd "/tmp/path with spaces" "ls"
```

路徑由 Server 安全處理引號，作為字面值，不展開 `$變數`、`~` 或 Shell 語法；含空白、單引號及以 `-` 開頭的路徑均支援，空路徑與控制字元會拒絕。絕對路徑從 IPCam 根目錄計算，相對路徑從原登入 Shell 的目錄計算，且不受 CDPATH 搜尋影響。

先切換目錄成功，才執行整條命令。目錄不存在時回報 stderr 與非零 Exit Code，連分號後的命令也不執行。此選項只影響本次 exec；下一次沒有 `--cwd` 時仍使用原登入 Shell 的目錄。不新增全域工作目錄狀態。

exec JSON 結果、status 最近結果、Monitor 原始事件會包含 `cwd`，未指定時為 null。精簡 Monitor 會在原始命令前顯示 `[cwd=路徑]`，完整模式可查看實際含目錄切換的包裝。新增選項後請重新啟動 Server，再使用更新的 Client。

指定 `--cwd` 時，Client 會先在同一條 TCP 連線送出唯讀 `hello`，確認 Server 宣告支援 cwd，才送出 exec。舊 Server 不支援時會明確回報 `cwd_unsupported`／提示重啟，原命令不會送出，避免舊程序忽略 cwd 後在錯誤目錄執行。一般未指定 cwd 的 exec 不增加握手。可用 status 的 `features` 清單是否包含 `cwd` 確認目前連線的 Server 支援此功能；欄位缺少時應重啟正在執行的 Server，更新磁碟上的程式不會自動更新已啟動的程序。

命令包裝的外層子 Shell 設置 INT trap，讓內層命令被 Ctrl+C 中斷後仍可輸出 Exit Code 與完成 Marker；不改動登入 Shell 的 trap。設備忽略 SIGINT、停用終端中斷或命令改動終端設定時，仍可能無法收到完成標記。

命令須為單行，含包裝上限 4096 bytes；多步驟可使用 `;` 或 `&&`。Timeout 預設 10 秒，可設 0.1～300 秒。輸出保留前 1 MiB，可用 `--max-output` 調整；超限後仍解析完成標記，明確回報截斷。

一般 exec 輸出至互動終端時，會原樣呈現 ANSI 顏色、游標移動、退格、CR／LF 等控制字元；Windows Console 會暫時啟用 ANSI／VT，顯示後還原主控台模式。Windows 上以 UTF-8 解碼，無效字元顯示為替代字元。輸出至管線／檔案時維持可讀跳脫格式，ESC、其他控制字元及無效 UTF-8 不會被直接執行。

exec 仍在命令完成或逾時後一次顯示已收集的輸出；需要即時互動畫面請使用 terminal 或 monitor。此顯示調整只需使用更新的 Client，無須重啟 Server。`--json` 維持結構化資料，提供原始 bytes 的 `output_b64`：

| 欄位 | 意義 |
| --- | --- |
| `id` | Server 產生的本次命令識別 |
| `state` | `completed`、`running` 或 `unknown` |
| `exit_code` | 只有 `completed` 才有 0～255 的值 |
| `reason` | 例如 `timeout`、`client_disconnected`、`uart_error` |
| `output_b64` | 標記間原始 UART bytes，移除包裝的分隔換行 |
| `output_complete` | 已完成且保留了完整輸出 |
| `output_truncated` | 是否超過保留上限 |
| `may_still_be_running` | 沒有觀察到完成時為 true |
| `interrupt_requested` | 是否曾對此命令嘗試傳送 Ctrl+C；不代表設備已停止 |
| `cwd` | 本次指定的 IPCam 工作目錄；未指定為 null |

CLI 退出碼：`0`＝完成、Exit Code 0 且輸出完整；`1`＝完成、Exit Code 非零且輸出完整；`2`＝拒絕、連線失敗、結果不明或輸出截斷；`130`＝Client Ctrl+C。設備的原始 Exit Code 請讀取 JSON。

### monitor

```bash
uart-client monitor
uart-client monitor --full
uart-client monitor --json
```

Monitor 唯讀訂閱連線後的事件，不回放歷史，提供兩種顯示模式：

| 模式 | 指令 | 顯示內容 |
| --- | --- | --- |
| 精簡（預設） | `uart-client monitor` | 顯示使用者原始命令、RX 結果與 Shell 提示符，不加時間、方向或命令狀態 |
| 完整 | `uart-client monitor --full` | 本機時區的毫秒時間、TX／RX 方向、寫入結果、命令完成與錯誤 |

精簡模式收到 TX 時顯示使用者原始指令，例如 `uname -a` 或 `ls`；指定 cwd 時加上 `[cwd=路徑]`，明確表示只影響該次命令，不代表登入 Shell 已改變目錄。中途加入正在執行的命令時也會顯示此標記。隱藏 Gateway 包裝的 Echo、開始／結束 Marker 與額外分隔換行，再連續顯示 RX 結果。即使設備停用 Echo，也會顯示原始命令一次，不會出現內部 `trap`、`printf` 或 `__UART_BEGIN_...`。

```text
~ # uname -a
Linux Network-Camera ... aarch64 GNU/Linux
~ # ls
c0  s0  s3
event  s1  s4
~ #
```

指令會接在目前已顯示的 Shell 提示符後面；若剛連線尚未收到提示符，會先顯示指令，不猜測設備的 Prompt。中途加入執行中的命令時，會先顯示目前原始指令，再接收後續輸出。命令從另一個終端的 exec 送出；Monitor 不接受鍵盤命令輸入。只有致命錯誤會另寫 stderr。

指定工作目錄時的畫面：

```text
~ # [cwd=/mnt/flash2/patch] ls
apply_patch_after  display.sh
~ # pwd
/mnt/ramdisk
~ #
```

`[cwd=…]` 是 Monitor 的顯示說明，不會作為 UART 指令送出；未指定 cwd 時不加標記。只更新此顯示方式時，重新開啟 Monitor 即可。

在 Monitor 視窗按 **Ctrl+C 會結束本機 Monitor 並回到 PowerShell／Shell**，不送出遠端中斷；要中斷 IPCam 命令請另用 `uart-client interrupt`。沒有 RX、或 NDJSON 只收到一部分時，也能停止 Monitor。讀取每 0.1 秒返回檢查中斷訊號，不以重新連線或丟棄半筆訊息處理等待。

過濾依目前命令的唯一 ID 判斷，支援折行 Echo、跨 RX 區塊的 Marker、連續 exec 與命令執行途中才加入 Monitor。只有收到匹配的開始 Marker 後才顯示命令輸出；開始之前的 Echo／資料暫不顯示。未執行 Gateway 命令時，RX 直接顯示；命令輸出的顏色、游標、CR／LF、二進位 bytes 及其他 ID 的類似 Marker 內容保留。完整模式與 `--json` 保留原始 Echo 與框架供查驗，Server Log 與 exec 的完成判讀不受顯示過濾影響。

Windows Console 在監控期間啟用 ANSI／VT，離開時還原設定。UART 分段不會拆壞 UTF-8 或 ANSI 控制序列。Windows 主控台逐步解碼 UTF-8，無效字元顯示為替代字元；精簡模式輸出至管線／檔案時保留過濾後的原始 bytes。更新後建議重新啟動 Server 與 Monitor，讓中途加入的 Monitor 能取得目前命令與標記解析前綴。

完整模式範例：

```text
[22:18:19.123] TX  （送出的 Shell 命令包裝）
[22:18:19.124] TX_RESULT  id="..." accepted_bytes=300
[22:18:19.150] RX  Linux IPCam ...
[22:18:19.153] DONE  id="..." exit_code=0
```

TX 顯示實際送出的完整包裝，interrupt 顯示為 `Ctrl+C (0x03)`。RX 以每個邏輯行第一筆資料的時間加前綴，不會因 UART 分段重複插入時間；Prompt 不必等到換行就會顯示。若 TX／狀態事件插入尚未結束的 RX 行，會先分行，再顯示事件，後續 RX 另起一行。

完整模式保留 ANSI SGR 顏色；為避免覆蓋時間戳記，游標移動、清畫面、退格及單獨 CR 等改為 `<ESC[3;5H>`、`<ESC[2J>`、`<BS>`、`<CR>` 文字。一般 CRLF 合為一個換行。需要原本的游標／畫面效果請用精簡模式。

`--json` 是另外的原始事件輸出，保留每個事件的 UTC 時間、序號及原始 `data_b64`，不執行控制序列；與 `--full` 互斥。需要完整保存事件請用此模式或 Server Log。

Monitor 不能寫 UART，也不能在同一連線改成 exec、interrupt 或 terminal。每個 Monitor 最多排隊 128 筆事件；太慢時移除訂閱並盡可能回報 `monitor_overflow`，持續阻塞的 TCP 連線則關閉。斷線後不補回缺口；慢速 Monitor 不阻塞 UART 廣播。

### terminal：同畫面輸入與接收

先重新啟動新版 Server，再於 PowerShell 執行：

```powershell
.\.venv\Scripts\uart-client.exe terminal
```

Linux 使用 `uart-client terminal`。需要真正的互動終端，不接受 stdin／stdout 管線；純接收或導出資料請使用 monitor。若畫面沒有提示符，可自行按 Enter；程式不會自動送探測指令。

此模式將鍵盤資料直接送到 UART，不包裝 Shell 命令、不加 Marker、不做本機 Echo，原樣顯示 UART RX。可以手動輸入帳密、回答互動提示、觀察 reboot 後的開機與登入畫面；顏色、游標、方向鍵、退格與 UTF-8 由終端處理。Enter 沿用 Server 的 `--newline` 設定，預設 CR。

| 按鍵 | 行為 |
| --- | --- |
| Ctrl+C | 傳送 `0x03` 給 IPCam，不退出本機 terminal |
| Ctrl+] | 離開本機 terminal，回到 PowerShell／Shell，不中斷設備程式 |

terminal 內的 `cd` 直接改變登入 Shell 的目錄，會保留至後續互動及未指定 cwd 的 exec；`exec --cwd` 仍只影響當次子 Shell。離開時清除尚未送出的鍵盤輸入，避免殘留帳密在本機 Shell 執行，並還原主控台的輸入／輸出模式。

**獨占與恢復：** terminal 取得獨占寫入權，其他 terminal、exec、外部 interrupt 都被拒絕；Monitor 仍可唯讀接收。已有正常執行中的 exec 時，先等它完成或明確 interrupt。逾時／Client 斷線留下的未知命令可以由 terminal 人工接手，原執行結果保持 unknown，不會偽造完成或重送。

離開或斷線後，Server 保留 `shell=uncertain` 並阻擋新的 exec。確認設備已登入 Shell、前景命令已結束後，明確執行：

```powershell
.\.venv\Scripts\uart-client.exe terminal --shell-ready
.\.venv\Scripts\uart-client.exe exec "pwd"
```

`--shell-ready` 是人工對目前狀態的確認，不進入終端、不探測或寫 UART；它恢復 exec 與正常 Log，不還原未知舊命令的 Exit Code。若尚未確定設備狀態，先重新進入 terminal 查看／恢復。Server 不會因看到 Prompt 或離開終端就自動確認。

**帳密與 Log：** 從 terminal 開始到明確 shell-ready 為止，Server Log 的 TX／RX 只保存時間、方向、序號與 byte 數，內容以 `redacted: true` 隱去，避免保存密碼或延遲 Echo。其他 Monitor 也只能看到隱去內容的互動 TX；完整模式顯示 `<互動輸入已隱去，N bytes>`。RX 仍原樣顯示，若設備本身回顯密碼，其他 Monitor 也會看到；此模式不猜測或修改設備的密碼回顯設定。請勿把登入中的 Monitor RX 自行導出至不適合保存帳密的位置。

每次互動寫入最多 4096 bytes，最多一筆未確認 TX，未送鍵盤佇列也最多 4096 bytes。Driver 接受不代表命令完成；寫入失敗、回覆逾時、連線中斷或 RX 佇列溢位都不重送／自動重連。UART 裝置被拔除時需修復後手動重啟，不會藉自動重開 COM 恢復輸入。

### status

```bash
uart-client status
```

輸出 JSON，包括 UART 狀態、COM、Baud Rate、最近 RX 時間、唯讀 Monitor 數、Log 錯誤，以及目前／最近一次命令的結果與輸出。`terminal_id` 是目前互動連線，`log_redacted` 表示 UART 日誌內容是否正在隱去。只保存一筆 exec 結果，不提供跨 Server 重啟的歷史查詢。

| Shell 狀態 | 意義 |
| --- | --- |
| `unknown` | 尚未觀察到任何命令完成，並非已確認 Shell 就緒 |
| `running` | 有命令等待完成標記 |
| `terminal` | 互動終端獨占寫入中 |
| `idle` | 最近一次命令已收到完成標記，沒有待確認的命令 |
| `uncertain` | 逾時、Client 斷線、UART 故障，或互動終端離開後尚未人工確認 |

`idle` 只是最近完成的證據，無法保證設備此刻沒有重啟或退出 Shell。status 不送 UART 探測指令；Server 啟動不會辨識先前遺留的命令，第一次 exec 前應確認設備已就緒。

### interrupt：送 Ctrl+C 到 IPCam

```powershell
.\.venv\Scripts\uart-client.exe interrupt
.\.venv\Scripts\uart-client.exe status
```

Linux 對應使用 `uart-client interrupt`。若 exec 正在等待，請另開終端送出；在 Monitor 或 exec 視窗按鍵盤 Ctrl+C 仍只停止本機 Client，不會自動傳到 IPCam。

Server 收到明確請求後只寫入一次 `0x03`，不附加換行、不重送原命令，也不在失敗／斷線後重送中斷。正常回覆為 JSON：

```json
{"type":"interrupt","id":"目前命令ID或null","state":"written_to_driver","accepted_bytes":1}
```

退出碼 `0` 只代表 Driver 已接受中斷字元，不代表 IPCam 已停止。原命令鎖保留至收到原本的完成 Marker；用 status 查詢並核對原命令 ID、Exit Code 及 `interrupt_requested`。常見的被 SIGINT 中斷 Exit Code 為 130，實際結果仍以完成 Marker 為準。

逾時／Client 斷線後仍可 interrupt。若沒有 Server 追蹤中的命令，也允許傳送，`id` 為 null，供中斷先前手動啟動的前景程式；此時只能從 Monitor 觀察 Shell 是否回復，不會憑傳送成功宣稱 Shell 已就緒。Ctrl+C 作用於設備終端當時的前景程式，不能保證中止忽略 SIGINT 的程式或背景工作。

UART 正在執行另一次寫入時，中斷回覆 `tx_busy`，不排隊；中斷寫入期間也不接受新 exec。寫入失敗／部分寫入回報結果不明並封鎖後續寫入。缺少完成 Marker 時仍維持鎖定，不以看到 Prompt 代替完成。舊 Server 必須重新啟動才有 interrupt；新版 INT trap 只適用於新版送出的命令。

## Timeout／斷線與恢復

- 同時只能有一個 exec；另一個立即收到 `busy`，不排隊。
- Timeout、Client Ctrl+C 或 Tunnel 斷線不等於設備命令停止。Server 保留命令鎖與解析器，繼續接收 UART；不重送、不自動送 Ctrl+C。需要中斷時明確執行 interrupt。
- 使用 status 查詢原命令，核對 `id`。之後收到原命令的完成 Marker，Server 更新結果並解除鎖。
- 可先明確執行 interrupt，再用 status 等待原命令完成。若一直沒有完成標記，後續 exec 一律拒絕；先由人確認設備、原命令已結束及 Shell 可用，再手動重啟 Server。
- UART 故障或部分寫入會標記結果不明並拒絕新命令；修復連線、確認設備狀態後重啟，不自動重開 COM。
- 重啟 Server 會遺失記憶體中的結果與命令鎖，**不會停止 IPCam 上的命令**，不能藉由重啟證明可以執行下一條命令。

## TCP 協定與 Log

TCP 使用 NDJSON：每行一個 JSON 物件，每個連線只執行一個操作，可在操作前先送一次唯讀 hello 確認功能。

```json
{"op":"exec","command":"uname -a","timeout":10,"max_output":1048576}
{"op":"exec","command":"ls","cwd":"/mnt/flash2/patch"}
{"op":"hello"}
{"op":"monitor"}
{"op":"status"}
{"op":"interrupt"}
{"op":"terminal"}
{"op":"terminal_ready","confirmed":true}
```

hello 回覆 `{"type":"hello","features":["cwd","terminal"]}`，不寫 UART；確認後在同一連線送 exec，避免跨連線檢查期間 Server 換版。exec 先收到 `accepted`（含 ID，代表已接受本次寫入嘗試），再收到 `result`；拒絕時收到 `error`，不送 UART。interrupt 回覆單筆 `interrupt` 或 `error`；傳送嘗試以 `tx`／`tx_result` 的 `action: interrupt` 記錄與廣播。Monitor 先收到 `monitor`，後續接收 `tx`、`rx`、`tx_result`、`completed` 等事件。每筆 NDJSON 上限 2 MiB，同時最多 16 個 TCP Client；錯誤請求不觸發 UART 寫入。

terminal 先回覆 `{"type":"terminal","id":"...","newline":"cr","log_redacted":true}`，之後同一連線雙向傳送。Client 必須等到此回覆後才送 `{"op":"terminal_write","data_b64":"..."}`，Server 回覆 `terminal_tx`／`accepted_bytes`，並持續推送 RX。`{"op":"terminal_close"}` 離開互動連線；只接受這兩種終端操作，不能切換成 exec。`terminal_ready` 是獨立的明確人工確認操作。Slow terminal 的 RX 佇列同樣有 128 筆上限，溢位會撤銷寫入並中斷連線，UART Reader 繼續接收。

預設 Log 為 `logs/uart.ndjson`，每檔約 5 MiB，保留三份輪替備份。記錄 UTC 時間、遞增序號、方向及 Base64 原始資料；`tx` 是送出嘗試，`tx_result.accepted_bytes` 才是 Driver 回報接受量，兩者都不能代替 Shell 完成標記。寫入例外可能無法得知接受了多少 bytes，一律結果不明。

可用 `--log 路徑` 調整。Log 寫入失敗會反映於 status 的 `log_error`，UART 接收仍繼續，但紀錄不再視為完整。Log 可能包含命令、輸出與機密；`logs/`、`.venv/` 不納入 Git。

互動終端及其未確認恢復期間為例外：TX／RX 日誌內容會隱去，不是完整原始擷取；狀態以 `log_redacted` 明示。

## 效能與緩衝

UART 收到第一個 byte 後，立即再讀取當時已在 Driver Buffer 的資料，合為一筆 RX；每筆最多 4096 bytes。不等待湊滿、不增加 sleep，也不改變 bytes 順序。若第二次讀取失敗，第一次已收到的 bytes 仍先送入 Log、Monitor 與 Marker 解析，再回報 UART 錯誤。

Monitor 每次 TCP recv 後，立即處理其中所有完整 NDJSON 訊息，集中寫入畫面並 flush 一次。三種顯示模式均保留事件順序與原有內容；只有半筆訊息時保留解析緩衝，已完整的 Prompt 不等待下一次資料。若後續訊息損壞，先交付前面合法的事件，再報錯。

這些調整降低 JSON、Log、TCP 與畫面更新的次數。Log 仍按每筆事件同步保存，沒有新增背景 Writer、認證流程或套件；Baud Rate、TCP 設定與 Marker 完成判讀保持原有行為。UART 115200 8N1 的理論上限仍是每方向 11520 bytes/s。

2026-11-14 在 Windows 11 以相同模擬資料比較優化前後：

| 項目 | 優化前 | 優化後 |
| --- | --- | --- |
| 128 KiB、64-byte burst 的 RX 事件數 | 4096 | 2048 |
| 同一資料的 Log 大小 | 514989 bytes | 347053 bytes |
| 五次測試的處理時間中位數 | 約 82 ms | 約 47 ms |
| 同次 TCP recv 含 512 筆 RX 時，精簡模式的畫面寫入／flush 次數 | 512 | 1 |

前後 UART bytes 的 SHA-256 相同，三種 Monitor 模式的顯示內容也完全相同。這是離線模擬、未限制 UART 線速的處理成本比較，不能視為實體 UART 或 SSH Tunnel 的傳輸速度提升；實際改善幅度取決於 Driver 分段與 TCP 封包到達情況。本機完整比較結果存於 `logs/performance-comparison.json`。

## 測試

不碰實體 UART 的基本測試：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Fake Serial＋真 TCP，涵蓋 TX/RX、Echo、分段／錯誤 Marker、Exit Code、二進位與大量輸出、Timeout、斷線、命令鎖、Monitor 唯讀／溢位、UART 讀取／部分寫入錯誤、慢速 TX 時持續 RX、Log 故障及 CLI 行為。

中斷測試另涵蓋一次性 `0x03`、Monitor 不能發送中斷、無追蹤命令時的中斷、逾時後中斷、請求斷線不重送、寫入失敗、慢速中斷期間 TX 互斥及 RX 持續接收。Monitor 測試涵蓋精簡模式的原始指令顯示、Echo／Marker 過濾與原始輸出保留、折行與逐 byte 分段、中途加入／重連、完整模式每行單一時間、分段 UTF-8／ANSI、即時 Prompt、控制碼文字化、命令事件與 JSON 互斥參數；另驗證三種模式在沒有 RX、以及 NDJSON 訊息未收完整時收到 Ctrl+C 都會退出且不寫 UART。效能調整另測試 RX 合併、4096-byte 上限、第二次讀取失敗不遺失已收到的 bytes、單 byte 即時交付、NDJSON 分批／錯誤／大小上限，以及三種模式減少 flush 後的輸出完整性。cwd 測試涵蓋路徑引號、CDPATH、目錄不存在不執行命令、當次作用域、CLI／協定／狀態與錯誤請求不寫 UART，另確認舊 Server 不支援 cwd 時不送 exec，以及功能檢查與命令共用同一連線。自動測試共 77 項通過；實際 POSIX Shell 測試需要 sh，Windows 會自動使用可用的 Git for Windows sh，沒有 sh 時會明確略過相關測試。先前也已在 Windows 主控台實際送出 Ctrl+C 確認 Monitor 返回退出碼 130。

以下實機測試會向指定 COM 寫入固定測試命令；需先關閉占用 COM／TCP 埠的 Server，並確認 IPCam 已登入可用 Shell：

```powershell
.\.venv\Scripts\python.exe tests/hardware_smoke.py --ports COM3 COM4
.\.venv\Scripts\python.exe tests/hardware_smoke.py --ports COM3 COM4 --interrupt
.\.venv\Scripts\python.exe tests/hardware_smoke.py --ports COM4 --tcp-port 18082 --interrupt --test-cwd
.\.venv\Scripts\python.exe tests/hardware_smoke.py --ports COM4 --tcp-port 18082 --interrupt --test-cwd --test-terminal
```

2026-11-13 在 Windows 11 完成：18 項自動測試通過，包含 Monitor 分段 ANSI／UTF-8、游標與換行 bytes 的完整性；另以 Windows 真實主控台確認顏色、中文分段與 CR 覆寫可正常顯示。COM3、COM4 依序使用 `127.0.0.1:18081`，均通過 COM 獨占、`uname -a`、stderr／Exit Code 7、引號、無結尾換行、二進位 bytes、Timeout 後 busy／遲到完成、Client 斷線後遲到完成，以及 Monitor TX/RX。每輪每個 COM 恰有 6 次 TX，沒有重送。實機測試程序已關閉，COM 已釋放；實機摘要與原始 Log 位於忽略 Git 的 `logs/`。

Linux、SSH Tunnel、USB 拔插本輪未驗證。最小版本建立於原本只有 README 的 `min` 分支；舊版 `work` 分支保留，沒有合併舊的 TUI、多 UART、Token／Lease、Go Client 或 SSH 安裝流程。

同日新增 interrupt 實機驗證：COM3 原本手動執行的時間迴圈，以一次 `0x03` 中斷並觀察到 `^C`／Shell 提示符。COM3、COM4 再依序通過執行中／Timeout 後中斷 `sleep 5`，兩種情況都收到原命令的完成 Marker、Exit Code 130，且後續 exec 成功。啟用 `--interrupt` 的每輪每個 COM 共 11 筆 TX，包含兩次單 byte 的 `0x03`，沒有自動重送。實機測試程序已關閉，COM 已釋放。

2026-11-14 效能調整後，COM3、COM4 均再次通過上述完整實機測試，包含二進位輸出、Exit Code 7、Timeout、Client 斷線、Monitor 與中斷後 Exit Code 130／再次 exec。每個 COM 仍恰有 11 筆 TX，沒有重送；測試程序已關閉、COM 已釋放。Linux 與 SSH Tunnel 本次仍未驗證。

同日新增 cwd 驗證：56 項自動測試通過（含 Windows 上的實際 Git sh，無略過），並以獨立測試 Server 在 COM4／127.0.0.1:18082 通過完整實機測試。包含 `--cwd /tmp` 的 pwd、無效目錄不執行整條命令、下一次 exec 維持原目錄，以及帶 cwd 的 sleep 可被 Ctrl+C 中斷並取得 Exit Code 130。此輪共 15 筆 TX，沒有重送；測試 Server 已關閉、COM4 已釋放，使用者原 Server 未停止。

後續處理 cwd 未生效的回報：實際連線的舊 Server 啟動時間早於 cwd 實作，回覆缺少 cwd 欄位。新增同連線功能確認並通過 58 項測試後，以暫時的新 Server 在 COM3 驗證 `--cwd /mnt/flash2/patch pwd` 正確回覆 `/mnt/flash2/patch`；同目錄 ls 顯示 `apply_patch_after`、`display.sh`，未指定 cwd 的下一次 pwd 仍回覆原目錄 `/mnt/ramdisk`。暫時測試 Server 已關閉，COM3 已釋放。

2026-11-14 新增 terminal：自動測試包含獨占、Monitor 唯讀、模擬開機／登入／密碼、不記錄 TX／RX 內容、原始 Ctrl+C、當次輸入確認、斷線／逾時不重送、部分寫入／RX 溢位拒絕繼續寫入、未知 exec 的人工接手，以及離開後明確 shell-ready。Windows 真實主控台已驗證中文、方向鍵、退格、Enter、Ctrl+C 傳出 0x03、Ctrl+] 返回，以及輸入／輸出模式還原。COM4／18082 實機通過 raw pwd、互動 cd 持續生效、Ctrl+C 中斷 sleep、還原原工作目錄及明確恢復 exec，共 25 筆 TX，沒有重送；測試 Server 已關閉並釋放 COM4。實機沒有 reboot 或重新登入，開機與帳密流程以 Fake UART 驗證；Linux 原生終端與 SSH 本輪未測。
