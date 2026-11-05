# 本機一鍵啟動器設計審查

## 兩條 UART 切片設計審查（2026-11-05）

使用者已授權實作前次建議的兩條只讀 UART。採單一 Gateway 與既有多 Channel 模型，CLI 新增 `local --select --count 2`，依序選擇、命名；`--save-profile 路徑` 保存無秘密的本機配置，`--profile 路徑` 重新核對裝置後啟動。單端點入口保留。配置契約見 [設定規格](configuration.md)。

- 啟動前拒絕重複端點、ID／名稱；取得全部端點鎖後才建立一個子程序。初次啟動採全部 Ready 才監控，任一占用／失敗則清理本次整組，避免將部分啟動誤報完整成功。
- 各監控沿用既有 `watch_client`，各用一條 SDK Session，避免兩個消費者搶讀同一 SDK 事件佇列；兩者連到同一 Gateway。全部授權僅 read。Ready 核對所有 Channel、UART 設定及每條 Session 的 Instance。
- 共用原有有界畫面 Writer，每批／每行帶 Channel ID 與名稱。各自維持 UTF-8 與 ANSI 解析狀態；多通道模式跳脫 CR／BS，顏色在每批輸出重設並於續批恢復，避免跨 Channel 改寫。這是區分來源的合併畫面，不是持續錄製或分割視窗 TUI。
- 運行中一個 Channel 遺失或訂閱失敗，顯示該 Channel 原因，另一條繼續接收；所有監控停止後才清理並回互動選單。共用畫面 Writer 失敗或 Gateway 整體退出會結束整組。Ctrl+C 清理兩條 SDK 與唯一自有子程序；不做執行中重連或重新 Confirm。
- 保存全部可用裝置條件。有序號時以序號及其他已存條件唯一解析目前 COM；無序號時保守沿用全部條件及原 COM。名稱不是板子識別。模糊或不符要求重新選擇，不能用舊編號代替確認。

本輪本機配置上限為兩條；Server 載入器與 Gateway 建構器的四條上限均不改動。兩條時 Ring 基線合計 8 MiB，SDK 事件佇列各 256 項／256 KiB，共用顯示佇列 256 項／256 KiB，未增加無界緩衝。五至六條需另量測吞吐、慢速顯示、SDK Session 數、開啟／關閉期限與記憶體，不能只放寬通道數字。API V1、Serial Server、控制線與 TX 政策不變。

驗收先用 Mock 配置／枚舉與兩個 PTY、真 Gateway 子程序驗證雙路接收、單路遺失後另一條繼續、重複啟動、第二條占用、取消及全部鎖釋放；Win11 雙實體 UART 與 30 分鐘持續接收另列待測。

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

原啟動器未包含互動選單；以下保留先前單端點擴充的設計。最新兩條 UART 設計見本文件開頭，遠端接管與 TUI Framework（框架）仍未加入。5～6 個 UART 仍需另驗證並調整既有 Gateway 的通道上限，不因新增選單就宣稱支援。

## 本輪：本機端點列出與選擇

- `uart ports` 只枚舉並顯示本機端點、描述、VID／PID、序號與位置，不需要 Token、不開啟 Serial、不啟動 Gateway。枚舉結果不代表端點未被其他程式占用。
- `uart local --select` 與 `--port` 互斥；原本不加參數的 COM3 行為保留。新增 `run-uart.cmd`，固定 COM3 的 `run-com3.cmd` 不變。全部沿用只讀啟動與既有監控。
- 選單在 `asyncio.run` 之前同步讀取互動輸入，沒有等待 input 的背景執行緒。只在 stdin 為 TTY 時允許選擇；支援編號、重新整理與取消，只有一個裝置也不自動開啟。清單輸出跳脫控制字元。
- 選擇編號後再次枚舉，要求端點仍唯一且已知身分欄位未改變。變更或消失時重新顯示選單，不把原編號套用到另一個裝置。啟動時再以既有 Adapter Selector 的 Port 與可用 VID／PID、序號、位置交集匹配。
- 裝置描述只屬本機 UI Metadata（顯示資訊），不加入 Core 或協定快照。重用原 Backend 的枚舉來源，不新增遠端枚舉 API。開啟後仍由 SDK 檢查 Ready、端點、只讀權限與 UART 安全設定。

這只能縮小選擇與開啟之間的競爭窗口，不能證明無序號的同名 COM 一定是原 Adapter，也不能辨識同 Adapter 換接不同板子。取消或輸入無效時不建立 Gateway；占用與開啟失敗由原啟動器處理。本輪已有 Mock 與 PTY 證據，使用者後續亦已回報居家 USB-UART／IPCAM 接收及終端顏色成功；CMD 選單與插拔等邊界情境仍需個別驗證。

## 驗證界線

自動化涵蓋鎖、Token 不進參數／檔案、Gateway／Channel 就緒、缺少端點、既有服務與端點占用、子程序提早退出、逾時、取消與正常清理。POSIX PTY（虛擬終端端點）可驗證真正 pySerial 與完整程序路徑，但不能代替 Windows 檔案鎖、CMD 主控台 Ctrl+C 或實體 COM3 的操作。後續使用者已回報 Win11 Quickstart、Ctrl+C 後重啟、重複啟動與 MobaXterm 占用交叉測試全部通過；實機證據另見狀態文件，並非 Agent 直接操作 Windows。

## 本輪：互動模式的手動恢復

Win11 Client 已是現有 SDK／CLI，不另建一套 Python Client。本輪延伸 `local --select`，不改線上協定：監控因裝置遺失、訂閱終止或已知啟動／連線錯誤結束後，等待原啟動器清理，再回到重新枚舉的選單。只有再次輸入編號才啟動，不自動沿用舊 COM、序號或選單編號。

每次監控各用一次 `asyncio.run`，輸入仍在事件迴圈之外；不建立 input 背景執行緒。首次選單 Enter 可取消，監控結束後的恢復選單 Enter 則只重新整理，避免殘留或誤按空白輸入讓程式退出。Ctrl+C、Q、正常到達 `--duration` 會離開整個互動流程；EOF 則以明確訊息說明輸入來源已結束後退出，不持續重讀 EOF。固定 `--port` 與遠端 watch 的退出語意保留。啟動參數錯誤在第一次選擇前拒絕，無法確認子程序已清理則以專用錯誤退出，不再次啟動。

裝置遺失會先顯示繁體中文原因；其他錯誤仍顯示原始錯誤類別，不一律當成 USB 拔除。返回選單時明示下一次是新 Gateway／資料流，舊 Ring 歷史不保留；不是 SSH Tunnel 重連到仍存活的同一個 Gateway。Token、Instance、Epoch 均不沿用，沒有 TX、Confirm、Lease 或自動重連。
