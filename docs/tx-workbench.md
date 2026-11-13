# TX 工作台設計與實機驗收

Container／Codex 需要可判讀的命令結果時，另提供 [shell-ready／exec／exec-status](shell-exec.md)，沿用本頁 TX 初始化與同一 SDK。Raw TX 的 Driver 結果與 Shell 完成證據分開；F3 遇到 Shell 收據會提示以 exec-status 查詢。

## 操作設計

日常使用 `uart tx`，或工作台 `uart menu` 的 X，連線既有 Gateway。這是明確選用的 TX 多窗格，仍可看 RX；`uart tui`、讀取／管理角色與 `tui --local` 維持原權限。TX 入口不啟動或接管 Gateway。

操作依序為：選 UART、核對端點／序號／目前 RX 並勾選確認目標、輸入文字或 Hex、明確選換行、預覽完整 bytes、點「送出一次」。表單 Enter 只進預覽，不送出；送出預覽預設聚焦取消。預設不附加換行、不解讀文字中的反斜線跳脫。沒有登入、自動指令、命令歷史或自動重送。

首次目標未 Confirm 時，核對後才呼叫既有 binding.confirm；它會建立新 Epoch，僅重新訂閱該路 RX，另一路繼續。已 Confirm 仍須本工作區人工核對，不重複 Confirm。核對只在目前 Instance／Revision／Epoch／端點資訊有效；插拔、重開、重選或重啟後須重做。相同 Adapter 換板無法自動識別，必須人工核對與原 Rebind／Confirm 流程。

「送出一次」才依目前快照取得獨占 Lease，SDK 續約、傳送、短期查詢結果，結束釋放租約並關閉這次 TX Session。表單停留期間不占 Lease，不搶占其他操作者；所有請求重新核對選定脈絡。結果查詢使用原 Instance／tx_id／Principal，不把新程序的查無資料當作未傳送。結果摘要只承諾 Driver 接受 bytes，IPCAM 是否執行須由使用者查看 RX。

結果保存在有界的本機操作收據，只含識別、時間、數量與結果，不含 Token 或完整命令。送出前無法保存收據就不傳送；不明收據不自動淘汰。工作台可選既有收據再查詢，不由收據恢復 Lease 或重送。退出／斷線不能保證撤回已開始的寫入。

## 權限規劃

固定來源新增明確 `tx` 角色，權限為指定通道的 read＋configure＋tx。read／manage 保持沒有 tx，預設仍 read；TX 角色必須明確初始化與安裝。Confirm 沿用 configure；目前協定沒有獨立 confirm-only 權限，因此 TX 操作者也具該通道既有 Binding 管理能力，僅授予可信任操作者。SDK 核對實際角色，角色錯配不降級。

建立新的 TX 部署可用 `auth init --enable-tx`，建議以不同連線名稱保存；原部署及秘密不覆寫。Gateway 副本與三份獨立憑證仍由原受保護檔案機制管理。傳輸包明確選 tx 才匯出；讀取與管理匯出不夾帶 TX 憑證。Client／Workbench 共用 SDK，Server／Protocol 欄位不變。

## Win11 安裝與一次設定

先安裝 `python -m pip install -e ".[server,tui]"`。以下用新名稱 tx-bench、不覆寫原部署；來源 COM 不同先修改私人副本。先正常關閉占用相同 COM／Port 的舊 Gateway 與 MobaXterm：

```powershell
.\.venv\Scripts\uart.exe auth init --gateway-config .\examples\gateway.dual.rx-reopen.toml --name tx-bench --enable-tx --tx-channels ch_uart_1
.\.venv\Scripts\uart.exe auth use tx-bench
$UartTxGateway = Join-Path $env:LOCALAPPDATA 'uart-gateway\connections\tx-bench\gateway.toml'
.\.venv\Scripts\uart-gatewayd.exe --serial --config $UartTxGateway
```

單路 COM3 來源可改為 examples\gateway.com3.toml。`--tx-channels ch_uart_1` 僅授予第一路 TX；第二路仍可用 read 憑證另開觀察視窗。若要在 TX 工作台同時監看兩路，可明確列 `--tx-channels ch_uart_1 ch_uart_2`（兩路皆有 TX 權限）。省略選項會授予來源所有通道，請按需要限制。

再開一個終端，日常只執行：

```powershell
.\.venv\Scripts\uart.exe tx
```

也可雙擊 `run-uart.cmd` 選 X，或 `uart menu` 選 X；另一入口為 `uart --access tx tui`。按 1／2 選 UART、F2 開傳送表單、F3 查收據。鍵盤核對目標可空白勾選，再 Tab 到確認；輸入 Enter 進預覽，預覽預設取消，點「送出一次」才傳送。Esc 返回，輸入框 q／y 是內容；Ctrl+C 清理 Client，不保證撤回已開始的寫入。

不必每天處理 Token。新部署有自己的 read／manage／tx 秘密；遠端 Client 改連此部署時須安全安裝對應角色包，舊部署憑證不適用。公司只觀察仍匯入 read；明確授權 TX 才 `auth export --name tx-bench --access tx`，走 [SSH 安裝流程](authentication.md)，在 dxan 用新連線名稱匯入。不會自動提升讀取權限。

## 結果與容量

F3 預設目前連線的最近收據，下拉選舊操作再點查詢，不用貼 TX ID。預設保存在使用者 UART 設定根目錄的 tx-receipts／連線名稱；`--connections-dir` 一起改根目錄，不混用其他連線的收據。每檔最多 4096 bytes、掃描最多 256 個目錄項目；同機以檔案鎖管理容量，各操作獨立檔名。每連線保留最近約 64 筆已知結果、最多 128 筆收據；不明／pending 不自動淘汰，達上限先查詢並人工封存。收據無命令、Payload、Token 或租約 Token，不由收據恢復 TX。

快取過期或 Gateway 重啟時，本次查詢可能 unknown；原收據已知終態仍保留，另標示本次查無紀錄，不能推論未執行。沒有先前證據則維持不明。進階可 `uart --access tx status <Channel> <TX-ID> --instance <原Instance>` 查詢。

SDK 共用 TxWorkflow／TxJournal；Agent 不得自行核對未知目標或送指令，必須由使用者明示。Server 仍以權限、Expected Context、Confirm、Lease 作最終保護。

## 實機驗收步驟

自動化先以真 SDK／TCP、Fake Serial 與 POSIX pySerial PTY 覆蓋精確 bytes、Confirm／租約／結果、取消與第二 Writer 拒絕、過期快照、拔除、斷線／逾時／部分寫入、查舊 Instance、不重送及另一路 RX 隔離。UI 另測輸入含 q／y 不觸發快捷鍵、Enter 不直接傳送及預覽取消零寫入。

實體驗收由使用者執行，以下目前均待測：

1. 第一輪建議拔除 IPCAM、只做已確認電位相容的 USB-UART TX／RX 自迴路，DTR／RTS 不接。明確選裝置及 TX 角色，確認目標後送自己輸入的短字串，先選不換行，比對 RX 與顯示 byte 數；再逐一驗 LF／CR／CRLF。
2. 在預覽取消、輸入框按 Enter／q／y、只做結果查詢；取消／查詢不得額外寫入。第二個 TX 工作台與其他租約持有者並存時，後者不得被搶占；結束租約後再人工送出。
3. 接回 IPCAM 前核對 TX／RX／GND、電位與當前 Shell 狀態。只用使用者明確指定的只讀命令及換行方式，先在預覽核對。不能用回顯或 written_to_driver 當作命令執行成功。
4. 傳送附近中斷 Client／Tunnel／USB，先查結果，不補送原指令；Gateway 重啟後查舊操作應維持不明語意。部分寫入／Driver timeout 的 TX blocked 不由工作台自動解除。
5. COM3 傳送、確認及失去連線時，COM4 RX 應持續；最後驗 Q／Ctrl+C 清理 Client 與租約，遠端 Gateway 繼續。

記錄作業系統、版本、Channel／COM／Adapter 序號、原 Instance／tx_id、送出 bytes、結果與實際 RX。日誌請去除帳密；模擬通過不視為 Win11、IPCAM 或公司 dxan 通過。實際命令／換行尚未確認前，不執行 IPCAM TX。
