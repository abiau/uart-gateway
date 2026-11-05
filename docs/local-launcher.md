# 本機一鍵啟動器設計審查

## 目標與選擇

本次新增只讀的本機啟動與監控封裝，重用既有 Gateway、設定載入器、Client SDK（用戶端開發套件）及 RX 顯示。沒有新的 UART Server、線上訊息或 API；不操作 Confirm（目標確認）、Lease（租約）或 TX。

| 方案 | 優點 | 缺點與判斷 |
| --- | --- | --- |
| CMD | 雙擊入口簡單，可找到 Repository（版本庫）的虛擬環境 | 認證、JSON、非同步 Ready（就緒）與精確清理不適合放在批次檔 |
| PowerShell | Windows 程序管理容易，既有教學可直接延伸 | 執行原則、引號及測試環境較複雜；容易把敏感資料放進命令列 |
| Python | 直接重用 SDK、標準函式庫與自動化測試 | 需要已安裝 Python 3.12 以上與專案；實體模式需 `[server]` |

採薄 CMD + Python。`run-com3.cmd` 只切換工作目錄、檢查 `.venv` 並在獨立主控台呼叫 `uart local --port COM3 --read-only --watch --pause-on-error`。Python 負責生命週期，Serial 與協定仍由原模組處理。首次安裝維持明確步驟，不在雙擊時自動下載套件或改動系統執行原則。

CMD 啟動 Python 視窗後即結束，避免 Ctrl+C 留在批次檔並出現終止批次工作的提示。啟動錯誤在互動視窗以「按 Enter 關閉」保留；正常 Ctrl+C 清理後結束。從既有 PowerShell 執行 CLI 時不需加 `--pause-on-error`。

## 啟動與 Ready 判定

1. 取得以標準化端點名稱為鍵的程序鎖。Windows 使用 `msvcrt` 檔案鎖，POSIX（類 Unix）測試環境使用 `flock`；鎖檔不含 Token（認證憑證），不藉刪除鎖檔搶占。
2. 檢查 127.0.0.1 的 Gateway Port 是否可用；預設 18081。既有 Listener（接聽端）存在就提示，不讀其憑證、不嘗試接管或停止。
3. 每次建立新 Token，只保留在啟動器記憶體及子程序環境。產生的臨時 TOML 只包含 `env:UART_GATEWAY_TOKEN` 參照、指定端點及 read 權限。Serial 設定沿用載入器的 115200／8N1／無流控、DTR／RTS false。
4. 呼叫原 `uart_gateway.server.app`，建立新程序群組。標準輸入是啟動器專有的停止管道；不傳 UART 命令。
5. 以 SDK 進行 Hello（握手）、認證及 Channel 查詢，並檢查子程序是否退出。有限期限內重試尚未開啟的 TCP；不是睡固定秒數後宣稱成功。
6. 只有 Channel ready、端點及只讀權限符合預期才監控。faulted／lost 立即顯示具體錯誤；PORT_BUSY 提示關閉占用 COM 的 MobaXterm 或其他程式，但不強制結束對方。

Gateway 另先保留 Loopback Socket，才開啟 Serial，完成初始化後才接受連線。這是內部啟動順序改善，避免 Port 已占用時仍嘗試開 COM；不改線上契約。

## 程序所有權與關閉

Parent（父程序）只持有自己建立的子程序物件與管道，沒有按名稱清除 python.exe、沒有根據 Port 反查並終止任意 PID（程序識別）。Ctrl+C 取消監控後，先關閉 SDK，再關閉該子程序的輸入管道。Gateway 觀察 EOF（輸入結束），執行原有 finally 清理 Channel 與 Handle（控制代碼）。此 EOF 模式只在啟動器子程序明確啟用，原手動啟動方式不受影響。

Windows 子程序群組不跟父程序同時處理 Ctrl+C；POSIX 子程序另開 Session（程序工作階段）。管道也讓父程序意外退出時能通知子程序。正常退出有有限等待；Driver 卡死等極端情況才 terminate／kill 本次子程序並回收，不碰其他 Gateway。應用層無法保證安全強殺卡死的 Driver 執行緒。

鎖在子程序結束後才釋放。既有手動 Gateway 不會持有 Launcher 鎖，但其 Listener 與 Serial 原生獨占仍會阻擋新啟動；不自動重用一個沒有憑證或不明所有者的 Gateway。

## CLI 與未來界線

本次提供 `uart local --port COM3 --read-only --watch`，預設只讀持續監控，Ctrl+C 停止。`--gateway-port` 可處理 18081 已被其他通道使用的情況，`--startup-timeout` 限制就緒等待；有限 `--duration` 供測試或短期擷取。

顯示沿用現有 SDK 與文字 Renderer（顯示器），先明示重播保留資料再接即時 RX。缺口、斷線與 Channel 終止必須可見；無資料不當作失敗，Ready 不等於板子一定正在輸出。

未來可在這個封裝入口加裝置選單或交由 TUI（文字介面）使用，但本次沒有互動枚舉、多 Channel 啟動、遠端接管或 TUI Framework（框架）。5～6 個 UART 仍需另驗證並調整既有 Gateway 的通道上限，不因新增啟動器就宣稱支援。

## 驗證界線

自動化涵蓋鎖、Token 不進參數／檔案、Gateway／Channel 就緒、缺少端點、既有服務與端點占用、子程序提早退出、逾時、取消與正常清理。POSIX PTY（虛擬終端端點）可驗證真正 pySerial 與完整程序路徑，但不能代替 Windows 檔案鎖、CMD 主控台 Ctrl+C 或實體 COM3 的操作。後續使用者已回報 Win11 Quickstart、Ctrl+C 後重啟、重複啟動與 MobaXterm 占用交叉測試全部通過；實機證據另見狀態文件，並非 Agent 直接操作 Windows。
