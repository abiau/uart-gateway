# 只讀互動式工作台

## 設計與範圍（2026-11-06）

使用者要求先開發、回家再測。本輪將既有 CLI 組成 `uart menu` 與薄雙擊入口 `run-workbench.cmd`，使用同步文字選單，所有監控仍經原 CLI／SDK／Launcher，不另外開 Serial 或重新實作協定。對應 Phase 2 的操作體驗切片；完整 TUI 與五至六條容量另行規劃。

工作台提供裝置清單、單／雙通道選擇後監看、選擇並錄製、使用既有端點 Profile，以及錄製目錄瀏覽、資訊、離線回看和文字匯出。`--recordings-dir` 預設 `recordings`，同時作為本次錄製根目錄及離線清單來源；每次重新枚舉，不自動開啟第一個裝置或最新錄製。可在選單中明確保存新 Profile；仍不覆寫既有檔案。

選單僅在 stdin／stdout 都是 TTY 時執行；輸入在 asyncio.run 之外，不留下等待 input 的背景執行緒。監控／回看中 Ctrl+C 等待既有流程清理後返回工作台；在選單提示時 Ctrl+C 離開整個工作台。Q／EOF 也可離開；EOF 不重試。一般操作錯誤回到選單，但 `LOCAL_CLEANUP_FAILED` 必須向外傳遞並結束工作台，不能清理不明還允許開下一個 Gateway。

錄製清單只掃描根目錄的 capture-* 與其 Channel 子目錄，數量有上限、最多顯示最近 50 個，超限時提供手動 Channel 目錄輸入；不遞迴掃描整顆磁碟。列表以目錄更新時間排序，並非完整性或來源板子的判斷，選定後仍交由原檔案檢查器驗證。顯示路徑會跳脫控制字元；所有選擇以結構化 argv 傳給同一 CLI，不使用 Shell Command。

工作台的本機 Gateway 仍使用每次新的臨時 Token；本機入口不接管公司遠端 Gateway。遠端 RX 可使用下述 --remote 模式，或原有帶 Token 參照的 channels／watch。工作台不提供 Confirm、Lease 或 TX 操作。

## 回家操作

更新 work 分支後，在專案根目錄雙擊 `run-workbench.cmd`，或執行：

```powershell
.\.venv\Scripts\uart.exe menu
```

選 1 列出端點，2 選擇並監看，3 選擇並錄製，4 使用既有 Profile，5 瀏覽錄製。單／雙通道仍由使用者明確選擇；選 3 的錄製存入指定根目錄下的新 Capture。選 5 選定一個 Channel 後，可重複檢查、回看及匯出；匯出必須指定新檔名。Q／直接 Enter 可返回或離開。

其他錄製位置可指定：

```powershell
.\.venv\Scripts\uart.exe menu --recordings-dir 'D:\uart-recordings'
```

測試順序為：用既有錄製檔檢查／回看／匯出，再用單 UART 錄製新資料，Ctrl+C 返回工作台，確認可重新啟動。最後於兩組設備齊備時另測雙 UART。自動化／PTY 不能代替 Win11 雙擊、主控台 Ctrl+C 與實體 USB 驅動驗證。

## 遠端模式（2026-11-06）

```sh
uart --host 127.0.0.1 --port 18081 --token-file <受保護的Token檔案> menu --remote
```

遠端模式只使用 SDK 連線既有 Gateway，第一次認證後列出有 read 權限的 Channel：ID、名稱、狀態、端點、錄製健康度及錯誤。輸入編號選擇；R 重新查詢或重新連線，正常清單 Q／Enter 離開。清單是上次查詢快照，不是持續更新的狀態；未就緒的 Channel 不建立訂閱。有 configure 與下述擴充能力時，可明確選擇單路 RX 重開。

選一個編號後 L／Enter 為即時接收，R 為最早仍保留的歷史＋即時接收，Q 返回。亦可依下節輸入兩個不同編號，同時監看最多兩條。使用現有終端過濾器與有界輸出；單路監看中 Ctrl+C 清理 Client 並返回清單，Gateway 保持執行。選單輸入處 Ctrl+C 結束工作台、退出 130，Q／EOF 正常退出 0。認證、連線或訂閱失敗，或查詢時所有通道都未就緒，會進入恢復選單：Enter 僅保持等待、不退出、不連線；R 才重新查詢。重新查到 ready 通道後恢復正常清單的 Enter 離開語意。

### 雙路同時監看（2026-11-07）

清單輸入 `1,2` 或 `1 2`，再選 L／Enter 即時或 R 歷史＋即時。只接收這次明確選擇的兩條，不自動加入其他通道；未就緒的一路先停留等待，另一路可正常監看。畫面每批／每行標明 Channel／名稱，各自保存 UTF-8／顏色狀態；CR／退格跳脫顯示，避免改寫來源標記或另一通道內容。這是合併文字畫面，沒有分割窗格或完整 TUI Framework。

| 指令（輸入後按 Enter） | 行為 |
| --- | --- |
| `R` | 查詢最新快照／權限，不重新訂閱或開啟 Serial；既有監看繼續 |
| `O 1`／`O 2` | 選擇工作區第一／第二路手動重開；需 configure、擴充能力、lost／faulted／closed 快照，再輸入 Y 確認 |
| `Y` | 僅在重開確認提示後有效；其他輸入取消該次操作，Q 可直接返回清單 |
| `S 1`／`S 2` | 對已停止且快照 ready 的一路手動重新監看即時資料；請先 R 更新；活動訂閱不重複建立 |
| Enter | 顯示目前狀態及操作說明，不連線或操作 Serial |
| `Q`／Ctrl+C | 清理雙路 Client 並返回外層清單，Gateway 繼續；按 R 查詢後再選，Q 離開工作台 |

O／S 的數字是本次工作區的第一／第二路，依進入時輸入的順序，與外層清單編號不必相同。等待確認、查詢與 Driver 重開期間，另一條訂閱持續接收。成功重開後只替該路建立監看連線，核對新快照並重播新 Epoch 仍保留的資料，再接即時；不接回舊 Epoch，也不重建另一條連線。失敗或結果不明不重試，先 R 查詢後再決定。重開等待中 Ctrl+C 也不代表 Server 已取消，返回後須先查詢結果。

每一路使用獨立 SDK Session／事件佇列，經既有 watch_client 核對 Instance／Revision／Epoch 與訂閱回覆，共用原有界 Writer；不新增 API。單路裝置／網路／快照錯誤只停止該路；無自動重連或失效後自動重新訂閱。R 發現不同 Instance 後禁止工作區內 S／O，必須 Q 返回並重新選擇。共享顯示失敗或容量不足時整組清理並明示錯誤，不能把缺資料當作正常顯示。

主控台採非阻塞輪詢：Windows 用 msvcrt 鍵盤介面，透過同一 Writer 顯示指令輸入；POSIX 保留原終端模式，以 select／單 byte 讀取完成指令。每次最多處理 128 字元，緩衝最多 128 字元，過長指令拒絕；沒有等待 input 的背景執行緒，也不把 UART 輸出視為指令。RX 持續輸出可能穿插指令提示，請輸入指令並按 Enter；不是固定位置的命令列。Windows 實際鍵盤、Ctrl+C 與視覺顯示仍須實測。

### USB 拔插與遠端恢復

USB 回到 Windows 枚舉清單，只代表端點再次可見。Gateway 目前不自動重開 Serial，原 Channel 在 device_lost 後仍為 lost。R 只重新連線／查詢 SDK 狀態，即使成功查詢同一 Gateway Instance，仍可能看到 DEVICE_MISSING。

本機 `menu` 的選 Port 流程擁有自己啟動的 Gateway，清理後可重新選擇並建立新程序。`menu --remote` 不管理 Gateway 程序；新版提供單通道手動 RX 重開：Gateway 須宣告 `channel.reopen_rx` 能力，該 Token 須有 read＋configure。選擇 lost／faulted／closed 通道後輸入 O，再明確 Y 確認；一般只讀 Token 不顯示操作，Server 也會拒絕呼叫。

操作前另建 SDK 連線並核對 Instance／Revision／Epoch；快照已變更時不開啟裝置，要求 R 重新選擇。成功時同一 Instance 下只有該 Channel 換成新 Epoch／unconfirmed，舊記憶體歷史清除，其他通道與錄製檔保留。成功後更新清單，由使用者再次選擇監看；失敗或結果未確認時不重試、不自動查詢，須按 R。請求可能已接受，不能把連線逾時當作未執行。核對原 Adapter 身分、關閉與 Driver 收斂規則見 [協定 9.2](protocol/v1.md#92-單通道手動-rx-重開擴充2026-11-06)。

舊 Gateway 沒有能力或目前 Token 沒有管理權限時，請由管理者操作單路恢復；全 Gateway 重啟僅作人工備援，須確認全部通道可中斷、沿用相同 Token，之後 R 查詢新 Instance。重啟不保留記憶體歷史。沒有背景自動重開，也不呼叫 Confirm、Lease 或 TX；Serial 參數與控制線設定不變。

每次查詢與監看各使用一條短期擁有的 SDK 連線，結束立即清理；輸入仍在事件迴圈之外。每次連線重新讀取 --token-file 或 --token-env（預設 UART_GATEWAY_TOKEN），更新 Token 來源後可按 R 重試，不將 Token 保存到工作台設定或輸出。

所選快照的 Instance／Binding Revision／Epoch 必須與新連線、查詢及訂閱回覆一致。不一致時取消訂閱、未顯示 RX，要求 R 更新並重新選擇；新 Token 若已無所選通道的 read 權限，也會拒絕並要求重新查詢。這個保護不能辨識同 Adapter 換接板子但未走 Binding 流程的情況。

--remote 與本機的 --recordings-dir 互斥。遠端選單不建立／停止 Gateway，不遠端修改錄製、UART 設定或取得 TX。錄製健康度是 Gateway 已回報的狀態，並非從 Client 啟用錄製。現有 run-workbench.cmd 保留本機入口。Win11 同機與 dxan 操作見 [Windows Quickstart](windows-quickstart.md)。
