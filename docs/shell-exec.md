# Container／Codex 的 UART Shell 命令

## 設計與範圍

Linux 與 Windows 使用同一 SDK／CLI；Container 經既有 SSH Tunnel 到 Windows Gateway，不直接開 COM。此功能供已登入、可接收命令的 POSIX sh／BusyBox ash 使用，不把開機 Log 當成 Shell 就緒，不猜測 Prompt、不自動登入或探測設備。

一次互動 `uart shell-ready <Channel> --newline lf` 核對目標、接線、已登入 Shell 與換行。沿用 TX 工作流程，必要時 Confirm，保存綁定目前 Instance／Revision／Epoch／Principal／Adapter 的無秘密授權。Codex 之後可 `uart exec <Channel> '命令'` 取得 JSON；脈絡改變須由人重新核對，不自動 Confirm 或放寬裝置條件。授權是本機操作偏好，Server 仍以 TX 權限與 Lease 最終裁決。

每次先取得整段操作的 Lease、訂閱即時 RX，再一次送出使用者明示命令的 POSIX 包裝。隨機開始／結束標記與 exit code 用於判讀；標記在送出字串中拆開，TTY echo 不會冒充完成。命令在子 Shell 執行、stdin 為 /dev/null、stdout／stderr 合併，不變更父 Shell 目錄／變數；互動命令、reboot／exec 取代 Shell 或永不結束的指令可能無完成標記。

RX 每筆核對世代與位移、記憶體／輸出有界。只回報「標記間觀測到的 UART bytes」，背景 Kernel／ps 訊息可能混入，不能當成獨立純 stdout。Driver 接受與 Shell 完成分開回報；缺口、裝置遺失、租約失效或缺標記時結果不明，不補送或自動送 Ctrl+C。

收據只保存原識別、起始 Cursor、標記、上限與結果 metadata，不預設保存命令或輸出。`uart exec-status <ID>` 用原 Instance／Principal／Cursor 重播查詢，只讀取、不取得 Lease 或傳送 bytes；Ring 淘汰／重啟可能無法還原輸出，保留先前已知完成證據。相同本機授權的未結案命令會阻擋下一次 exec；查詢結案或人工確認 Shell 已恢復後重新 shell-ready，不能以新 ID 自動重送。

實體 IPCAM、Windows COM、公司 dxan／SSH、新換行與 Shell 相容性目前待測；Fake／PTY 的成功不代表實機完成。既有 UART RX、TUI、Raw TX、錄製格式與 Server／Protocol 不變。

## 第一次設定

1. Windows 依 [TX 初始化](tx-workbench.md) 用新名稱建立明確啟用 TX 的 Gateway，僅授權需要的 Channel；先關閉占用相同 COM 的舊程序，再啟動這份獨立 Gateway。只讀 local Launcher 不提供 TX；統一工作台的 X 另使用獨立 TX 憑證。
2. Container 安裝本版 CLI：`.venv/bin/python -m pip install -e .`，不需要 Windows、pySerial、Textual 或 GUI。沿用既有 SSH Tunnel 到 Windows Loopback；家庭同機 Client 也可走 Loopback。
3. 依 [固定認證安裝](authentication.md) 安全匯出並匯入 **tx** 角色包，選定連線，例如 `.venv/bin/uart auth use company`。原 read 包不能用來傳送；不要每天重建秘密或把 Token 放在 argv。`--connection company` 也可在子命令之前明確指定連線。
4. 人工核對 USB-UART／板子接線，確認 IPCAM 已登入可用的 POSIX sh／BusyBox ash，沒有尚在執行的命令，並先確認目標接受的換行。接著在互動終端執行：

```sh
.venv/bin/uart shell-ready ch_uart_1 --newline lf
```

LF 是範例，實際使用可選 cr 或 crlf。核對畫面上的 Channel、COM、序號、Instance、Revision 與 Epoch，確認後輸入 Y；其他輸入取消，不 Confirm、不寫 UART。這一步只設定資格，沒有測試指令、自動登入或 Shell 偵測。未 Confirm 時沿用既有 Confirm，會切換該路 Epoch，同通道 TUI／監看需人工重新訂閱；已 Confirm 則不再次切換。

Gateway 重啟、USB 插拔後手動重開、Rebind／Confirm 使脈絡改變時，必須重新由人核對。相同 USB-UART 換接另一塊板子無法自動辨識，應先依原工作流程重新綁定。非互動 Codex 不應呼叫 authorize 或自動替人回答 Y。

## 日常執行

在已核對的目標上，以使用者明確指定的命令執行；以下 uname 僅為只讀驗收範例：

```sh
.venv/bin/uart exec ch_uart_1 'uname -a'
```

預設 stdout 是一筆 JSON，供 Codex 讀取。人工想看文字可加 `--text`；它跳脫控制字元，避免直接執行裝置提供的終端控制序列。命令需單行、不含隱藏控制字元，整段包裝含換行最多 4096 bytes 或 Gateway 宣告的較小上限；不拆分命令。Shell 引號由 SDK 處理，不在 Container 執行命令內容。

每條命令在新的子 Shell 執行；要換目錄請將 `cd ... && ...` 放在同一條命令內，不會保留到下次 exec。stdin=/dev/null，不適用需要輸入密碼、互動確認或接管終端的程式。預設等待 10 秒，`--timeout` 可設 0.1～300 秒；輸出保留前 65536 bytes，`--max-output` 可設 1～1048576。截斷後仍持續接收完成標記。

### JSON 判讀

| 欄位 | 意義 |
| --- | --- |
| `execution_id` | 原操作識別，也是原 tx_id；後續只查這個 ID |
| `state` | completed：連續 RX 看見匹配的完成標記；not_sent：未送出；unknown：不能判定完成 |
| `exit_code` | 只有 completed 才有 0～255；0 通常表示指令成功，非零是目標指令的結果 |
| `output_text`／`output_b64` | 標記間 UTF-8 可讀版本／原 bytes 的 Base64；查無原歷史可能為 null |
| `output_complete`／`output_truncated` | 輸出有無完整還原／是否超過上限；完成不等於輸出完整 |
| `output_scope`／`background_output_possible` | 固定 uart_between_markers／true；可能混有 Kernel、ps 或其他背景輸出，並非分離的純 stdout |
| `may_still_be_running` | unknown 時不能假設命令停止，亦不自動送 Ctrl+C |
| `tx_result`／`tx_query` | 原 Driver 接受結果／最近查詢；written_to_driver 本身不能證明 Shell 完成 |
| `receipt_saved`／`reason_code` | 本機結果是否成功保存／此次限制或失敗原因 |

CLI 退出碼：0＝已完成且目標 exit code 0；1＝已完成且目標非零；2＝未送出或結果不明；130＝使用者中止等待。即使退出碼 0，也需依用途檢查 output_complete／output_truncated，不能用部分輸出作完整解析。此標記是已核對 Shell 的操作協調，並非裝置身分的加密證明。

## 逾時／斷線後

保留 JSON 的 execution_id，查原操作，**不要重跑 exec**：

```sh
.venv/bin/uart exec-status 由前次JSON取得的execution_id
```

需要等原命令遲到的完成輸出時，加 `--wait 10`。查詢先讀原 Cursor 到當時歷史尾端，再最多等待指定秒數的新資料；全程不取得 Lease、不傳 UART bytes。不明命令的本機收據會阻擋相同授權下的新 exec；查詢觀察到完成後才解除。若無法結案，由人核對設備與 Shell 已恢復、前次命令已結束後重新 shell-ready，保存新的核對資格；不由 Agent 自動重新核對或重送。

Gateway Ring 歷史淘汰、程式重啟或世代切換時，不能保證找回輸出。先前已知的完成／exit code 不會因查無歷史而抹除，但 output_complete 會是 false；原來不明的結果不會變成成功。中止等待只清理 Client／租約，無法撤回已開始的命令。跨主機、不同連線名稱或明確 Raw TX 不共用本機攔阻；租約不是持久 Shell 工作鎖，請協調操作者。

收據不保存命令或輸出，因此 exec-status 依賴原 Gateway 的有限 RX 歷史；必要的完整保留請使用既有錄製。目標 Shell 的歷史、UART echo 或已啟用的 RX 錄製仍可能保存送出的命令，不因本機收據省略而消失。

## SDK 使用

CLI 與 Codex 可直接使用同一 ShellRunner。以下示範讀取既有人工 shell-ready 的資格，不替人啟用：

```python
from uart_gateway.client import ShellRunner, ShellTargets, TxJournal, resolve_connection
from uart_gateway.client.connection import config_directory

settings = resolve_connection(access="tx")
root = config_directory()
runner = ShellRunner(
    settings.connect,
    TxJournal(root / "tx-receipts" / settings.name),
    ShellTargets(root / "shell-targets" / settings.name),
)
# 在呼叫端既有 async 函式中：
result = await runner.execute("ch_uart_1", "uname -a")
value = result.as_dict()
# 只查原次執行：await runner.status(value["execution_id"])
```

自訂認證根目錄時 resolve_connection 與兩個儲存目錄必須一致。SDK 取消會丟出攜帶 result 的 ShellCancelled；依原 execution_id 查詢，不重送。底層 Client、Confirm、Lease／fencing、Expected Context 與 Protocol 均沿用既有實作。

## 實機驗收：目前待測

先完成 [TX 迴路與權限驗收](tx-workbench.md)，再由人確認 IPCAM 的 Shell／接線／換行。不要將以下待測步驟記為已通過：

1. 互動 shell-ready 取消時沒有 UART bytes；完成核對後，用明確允許的只讀命令（例如 uname -a）核對 JSON completed、exit code 0 與實際型號／版本輸出。
2. 使用目標已確認可用的無副作用非零命令（例如子 Shell 的 exit 7），核對目標 exit code 7、CLI 退出碼 1；再 exec-status 同一 ID，比對輸出，設備不得再次執行。
3. 使用操作者同意的短暫等待命令，將 --timeout 設較短；結果應 unknown、不自動重送。等命令自然結束後 exec-status 應可結案，未結案前新 exec 應拒絕。
4. COM3 執行時 COM4 的 RX 應持續；其他 Writer 不得搶租約。中途拔 USB／斷 Tunnel 後結果不能宣稱成功；重連只查原 ID，重新開啟通道後需人工重新核對。
5. 分別在家用 Loopback、公司 dxan＋SSH 路徑執行；記錄版本、Channel／Adapter、Instance／execution_id、輸出與結果。Windows ACL／檔案鎖與實體 UART 行為仍須分開驗收。
