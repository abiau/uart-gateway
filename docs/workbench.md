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

遠端模式只使用 SDK 連線既有 Gateway，第一次認證後列出有 read 權限的 Channel：ID、名稱、狀態、端點、錄製健康度及錯誤。輸入編號選擇；R 重新查詢或重新連線，Q／Enter 離開。清單是上次查詢快照，不是持續更新的狀態；未就緒的 Channel 只顯示原因，不建立訂閱。

選擇後 L／Enter 為即時接收，R 為最早仍保留的歷史＋即時接收，Q 返回。一次監看一條，使用現有終端過濾器與有界輸出，直到 Ctrl+C 或訂閱結束。監看中 Ctrl+C 清理 Client 並返回清單，Gateway 保持執行；選單輸入處 Ctrl+C 結束工作台、退出 130，Q／Enter／EOF 正常退出 0。認證、連線或訂閱失敗後必須按 R 才重新連線，不自動重試。

每次查詢與監看各使用一條短期擁有的 SDK 連線，結束立即清理；輸入仍在事件迴圈之外。每次連線重新讀取 --token-file 或 --token-env（預設 UART_GATEWAY_TOKEN），更新 Token 來源後可按 R 重試，不將 Token 保存到工作台設定或輸出。

所選快照的 Instance／Binding Revision／Epoch 必須與新連線、查詢及訂閱回覆一致。不一致時取消訂閱、未顯示 RX，要求 R 更新並重新選擇；新 Token 若已無所選通道的 read 權限，也會拒絕並要求重新查詢。這個保護不能辨識同 Adapter 換接板子但未走 Binding 流程的情況。

--remote 與本機的 --recordings-dir 互斥。遠端選單不建立／停止 Gateway，不遠端修改錄製、UART 設定或取得 TX。錄製健康度是 Gateway 已回報的狀態，並非從 Client 啟用錄製。現有 run-workbench.cmd 保留本機入口。Win11 同機與 dxan 操作見 [Windows Quickstart](windows-quickstart.md)。
