# UART Gateway 協作規則

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

本文件規範在本 Repository（版本庫）工作的 Agent（代理工具）與開發者。所有人類閱讀的文件、註解與說明使用繁體中文（台灣用語）；程式識別名稱、API 與 Protocol Field（協定欄位）保留英文。

## 目標與目前授權範圍

建立可長期維護的 UART 開發工具：Windows 獨占實體 COM Port，Linux 的 CLI（命令列介面）、TUI（文字使用者介面）及未來 Agent 透過共同 Client SDK（用戶端開發套件）操作。

Phase 0 基線已核准，Phase 1 已實作 Gateway、SDK／CLI、本機只讀啟動器、重播修正與可選終端顯示。使用者已回報 Win11 本機 RX、啟動器正常／占用流程、dxan 遠端 RX／重播、顏色／換行、再次監控及 Tunnel 中斷後手動恢復成功。2026-11-05 使用者在家無法連到 d3s1，明確授權先 commit／push 保存驗收紀錄與交接文件。使用者後續明確要求開始實作本機 COM Port 列出／選擇，已完成單端點選單與 Mock／PTY 測試；使用者已回報居家 USB-UART／IPCAM 接收與終端顏色成功；後續回報 USB 拔插時監控中斷，IPCAM 重新上電仍持續顯示；取消／重新整理／占用及完整插拔清理／恢復仍待驗證。30 分鐘本機接收由使用者延後，公司遠端持續接收亦待測。使用者現已授權繼續實作中斷後的手動恢復：清理後回選單，由使用者重新選擇；Win11 Python Client 沿用原 SDK／CLI，不另建一套。不新增 TX、多 Channel 啟動或自動重連；完整進度及驗證邊界以 status.md 為準。

## 開始工作前的閱讀順序

單通道 RX 恢復授權補充（2026-11-06）：使用者明確要求「先補日誌，再做單通道手動 RX 重開」。核准獨立 Gateway 狀態日誌，以及使用 configure 權限與 Expected Context 的單通道恢復擴充；沿用原裝置條件、核對已知身分、清理舊 Handle、新 Epoch，維持未 Confirm。CLI／遠端工作台須明確人工觸發，不因 R 或 USB 插回自動操作；不重啟其他通道、不取得 Lease、不發送 UART 指令。此補充允許此單一管理 API，其他遠端設定、完整 reload 及 TX 不屬本輪。

遠端工作台授權補充（2026-11-06）：使用者已選擇沿用 CLI 位址／Port／Token 參照，第一版一次一條 Channel，並核准所提計畫。新增 menu --remote 時重用 SDK 與 watch_client，不管理 Gateway 程序或自動重連；Instance／Binding Revision／Epoch 與訂閱回覆必須核對，不符時不顯示 RX 並要求重選。此模式不提供錄製設定、Confirm、Lease 或 TX。

工作台授權補充（2026-11-06）：使用者要求先開發可回家測的下一步，已延伸為本機只讀文字工作台，重用既有 CLI／SDK／Launcher 與離線錄製工具。不得以一般退出碼吞掉 LauncherCleanupError 後繼續啟動；清理不明必須結束工作台。完整 TUI／遠端設定 API／TX 不屬此切片。

後續授權補充：使用者目前沒有兩個 UART，雙實體驗證延後到公司，不標為成功；並核准接續實作只讀 RX 錄製。沿用 Gateway 端 Recorder、明確啟用、有界交接與容量限制，檔案失敗須回報完整性；不得新增 UART TX。錄製格式以 docs/recording.md 為準，單實體錄製與 30 分鐘測試也需另行回報。

最新授權補充（2026-11-05）：使用者已確認 USB 拔除後返回並停留於選單成功，並以「好的。請實做。」核准兩條只讀 UART 的下一步。沿用單一 Gateway／多 Channel，新增互動選兩端點與命名、保存／重用無秘密配置、分來源顯示與單路故障隔離；先以 Mock／PTY 驗證。本機新配置上限兩條，Server／Gateway 的四條上限不改；五至六條容量、完整 TUI、TX、自動重連與 30 分鐘接收尚未完成。此補充取代上段對本輪多 Channel 啟動的排除，其他架構及操作規則仍適用。

1. [README](README.md)：專案入口。
2. [目前狀態](docs/status.md)：真實進度、核准清單及已知差異。
3. [系統架構](docs/architecture.md)：責任、依賴與生命週期。
4. [開發路線圖](docs/roadmap.md)：本次所屬階段、驗收及排除範圍。
5. 依任務閱讀 [協定 V1](docs/protocol/v1.md)、[設定](docs/configuration.md)、[部署](docs/deployment.md)、[開發規範](docs/development.md) 及相關 ADR（架構決策紀錄）。

任何實作前都必須完成相關文件閱讀，檢查目前分支、Git Status（版本庫狀態）、既有程式與測試。不得假裝未讀過的檔案已受檢查，也不得因背景測試成功而宣稱本版本庫已實作。

## 架構與模組依賴規則

- 維持單一 Windows Gateway Process（程序）與內部模組化；不得擅自拆成微服務。
- Core（核心領域邏輯）不依賴 TCP Server、Client、pySerial 或 TUI。具體 I/O 實作透過 Core 定義的必要介面接入。
- Protocol（通訊協定）不依賴 Server 或 Client；Wire Model（線上資料模型）不反向污染 Core。
- CLI、TUI、未來 MCP（模型內容協定）介面及 Firmware Workflow（韌體工作流程）共用 SDK，不得繞過 SDK 操作 Core 或 Serial。
- Serial I/O 不得阻塞 asyncio Event Loop（事件迴圈）。每個 Channel（邏輯通道）獨立管理狀態、Buffer（緩衝區）、Queue（佇列）及 TX Ownership（傳送操作權）。
- 所有 RX/TX Queue 必須有上限；慢速 Client 不得阻塞 UART Reader（接收工作路徑）。
- TX 使用 Exclusive Lease（獨占租約）、Fencing Token（隔離舊持有者的序號）與單一 TX Worker（傳送工作路徑）。Binding（綁定）及 Stream Epoch（資料流世代）變更必須使舊操作失效。
- COM 名稱不是永久裝置識別。不能假設能自動偵測同一 Adapter（轉接器）換接另一塊 Board（板子）。
- SSH Tunnel 不取代應用層認證。Recorder（錄製器）失敗時不得宣稱 Capture（擷取紀錄）完整。

## 協定相容性規則

[協定 V1](docs/protocol/v1.md) 是唯一線上格式來源。修改訊息、欄位、錯誤碼、時間或重連語意時，先分析相容性並同步更新規格與契約測試。套件版本與協定版本分開。

不得假設 UART bytes 是 UTF-8；不得忽略 Gap（資料缺口）、把 Replay（重播）偽裝成即時資料，或因 TX Timeout（逾時）自動重送。Client 必須能觀察 Stream Epoch 與 Gateway Instance（程序執行個體）的變更。

## 程式撰寫與例外處理規範

未來經授權實作時，採清楚型別、有限責任及明確資源生命週期。函式與模組命名保留英文；文件字串及註解使用繁體中文。不得在 import 時開啟 COM、建立連線或載入憑證。

阻塞 I/O 放到受管理的工作路徑；取消、斷線及關閉流程必須可重複清理。例外應轉成具體領域或協定錯誤，不使用空的廣泛捕捉掩蓋失敗。不得將憑證、完整環境變數或預設完整 TX Payload（負載）寫入一般 Log（紀錄）。詳細規範見 [開發文件](docs/development.md)。

## 測試要求

執行與變更相關且有判別能力的檢查；不要以沒有測試失敗代表功能已被驗證。Protocol 與生命週期變更須覆蓋錯誤、界限及相容情境。

沒有實體 UART 時使用 Fake Serial（可控制的模擬裝置）與 Fake Clock（可控制的時鐘），搭配真實 TCP Server 做整合測試；Windows 裝置行為另列硬體驗證。測試層次與候選驗收見 [開發規範](docs/development.md) 與 [路線圖](docs/roadmap.md)。Phase 0 只做文件與範例檢查，不建立產品測試程式。

## 新需求、架構變更與重構

先將新需求對應到現有模組、介面、階段與驗收條件。優先在既有邊界內做最小必要變更，不因新增一個 API 就重寫 Server、SDK 或整個目錄。

若需求與已確認原則衝突，先記錄問題、影響、替代方案及建議，再依使用者授權處理；不得直接大規模重寫。只有可說明的重複、耦合、缺陷或新需求才支持重構。不要提前加入沒有使用者的 Framework（框架）、Plugin（外掛）系統或空目錄。

ADR 狀態只有 `Proposed`（提議中）、`Accepted`（已正式接受）、`Superseded`（已被取代）。審查建議不等於核准；只有可追溯的使用者同意才能將整份 ADR 標為 Accepted。已接受的決策變更應有新 ADR 並建立取代關係，不抹去歷史理由。

## 文件更新與權威來源

修改功能時同步更新該資訊的唯一權威文件：線上格式在 Protocol、參數在 Configuration（設定）、現行責任在 Architecture（架構）、部署步驟在 Deployment（部署）、驗收在 Roadmap（路線圖）。README 只保留入口摘要，ADR 記錄理由。不要建立完整內容互相重複的根目錄規格副本。

完成一次工作後更新 [status.md](docs/status.md)：日期、實際變更、證據、未執行檢查、已知限制及待核准事項。明確區分 `Planned`（預計開發）、`In Progress`（開發中）、`Implemented`（已實作）、`Tested`（已測試）、`Verified`（已實際驗證）；實作不等於測試，文件檢查不等於硬體驗證。

## Git 操作

開始與結束時檢查分支及工作目錄，保留無關的使用者變更。不得把私人設定、憑證或 UART 錄製加入版本庫。Commit、push、歷史改寫及部署必須在使用者授權範圍內；2026-11-03 的文件保存已完成；後續提交依使用者當次授權範圍進行。不得為了形成交付假象而自行建立提交或將未核准決策視為完成。
