# UART Gateway 協作規則

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

本文件規範在本 Repository（版本庫）工作的 Agent（代理工具）與開發者。所有人類閱讀的文件、註解與說明使用繁體中文（台灣用語）；程式識別名稱、API 與 Protocol Field（協定欄位）保留英文。

## 目標與目前授權範圍

建立可長期維護的 UART 開發工具：Windows 獨占實體 COM Port，Linux 的 CLI（命令列介面）、TUI（文字使用者介面）及未來 Agent 透過共同 Client SDK（用戶端開發套件）操作。

Phase 0 基線已核准，Phase 1 已實作 Gateway、SDK／CLI、本機只讀啟動器、重播修正與可選終端顯示。使用者已回報 Win11 本機 RX、啟動器正常／占用流程、dxan 遠端 RX／重播、顏色／換行、再次監控及 Tunnel 中斷後手動恢復成功。2026-11-05 使用者在家無法連到 d3s1，明確授權先 commit／push 保存驗收紀錄與交接文件。使用者後續明確要求開始實作本機 COM Port 列出／選擇，已完成單端點選單與 Mock／PTY 測試；使用者已回報居家 USB-UART／IPCAM 接收與終端顏色成功；後續回報 USB 拔插時監控中斷，IPCAM 重新上電仍持續顯示；取消／重新整理／占用及完整插拔清理／恢復仍待驗證。30 分鐘本機接收由使用者延後，公司遠端持續接收亦待測。使用者現已授權繼續實作中斷後的手動恢復：清理後回選單，由使用者重新選擇；Win11 Python Client 沿用原 SDK／CLI，不另建一套。不新增 TX、多 Channel 啟動或自動重連；完整進度及驗證邊界以 status.md 為準。

## 開始工作前的閱讀順序

遠端操作簡化授權補充（2026-11-12）：使用者於討論 Windows 一次設定、固定憑證與 Linux 單一入口後明確要求「請開始實作」。本輪沿用 SDK／Protocol／角色權限，新增 uart 預設遠端入口、通道偏好、Windows 互動設定與固定 Gateway／SSH 啟動。SSH 安裝使用明確目的地與角色，保留主機指紋核對，不覆蓋使用者自己的 Linux 入口，不修改容器網路／掛載；預設讀取，TX 明確選用且保留原核對／Confirm／Lease。換選 UART 保留固定秘密，新增本機通道仍上限兩路，TX 範圍不自動擴大；公司 SSH 與實體 UART 驗證另列。使用者後續以「commit/push」明確授權本輪功能、前置檢查逾時修正、測試與文件提交並推送至 origin/work。

Container Shell 執行授權補充（2026-11-07）：使用者明確要求實作 Linux Container／Codex 透過 UART Client 執行 IPCAM 指令。核准 CLI／SDK 的已登入 POSIX sh／BusyBox ash 單次命令包裝、完成標記／exit code、有界 RX 擷取、逾時與原操作查詢；先由人互動核對目標、Shell 狀態與換行並保存目前脈絡，非互動 exec 不自行 Confirm。租約涵蓋整次命令等待，未知不重送、不自動登入／送 Ctrl+C／更新韌體；連續性失效不宣稱命令成功。保留原 SDK 認證／TX／Expected Context 與 Protocol，實體 IPCAM／SSH 未測必須另列。後續使用者已明確授權將本輪與前輪 TX 工作台的程式、測試與文件提交並推送至 origin/work。

TX 工作台授權補充（2026-11-07）：使用者明確要求整合既有 Confirm／Lease／傳送／結果查詢，另規劃 TX 權限與實機驗收，優先簡單操作。核准明確選用的獨立 TX 角色及薄 SDK 工作流程／TUI 表單；預設讀取、管理、本機 RX Launcher 權限不升級。送出前人工核對目標、預覽精確 bytes 與換行，單次操作才取得租約，結果不明只查詢、不自動重送；保留 Instance／Revision／Epoch、租約 fencing 與原 Protocol。模擬／PTY 與實體 COM 驗收分開，沒有實機連線不得聲稱實機通過；不做自動登入、韌體更新或自動執行 UART 指令。

多窗格 TUI 授權補充（2026-11-07）：使用者明確要求各 UART 獨立畫面、固定狀態列及操作區，優先簡單操作。核准薄 Textual 介面及可選依賴，沿用 SDK／認證／原 Launcher；遠端最多 Gateway 現有四路，本機仍一至兩路。資料與操作不可混寫，畫面／解析／事件暫存有界，舊畫面截斷須明示；手動 RX 重開仍需 configure、快照核對與確認，不新增 TX、控制線、自動重連或線上契約。Windows 真終端及實體 UART 驗證必須另列。後續使用者已明確授權將本輪程式、測試與文件提交並推送至 origin/work。

SDK 自動認證授權補充（2026-11-07）：使用者核准保留認證、取消日常人工 Token 操作。新增固定的受保護遠端憑證來源、一次安全初始化及讀取／管理選用；CLI、Workbench 與 Codex 使用同一 SDK 載入流程。原本機臨時憑證及直接 SDK 介面保留，管理不自動取得 tx；真正 TX 仍需原權限、Confirm、Lease 與脈絡核對。不得新增匿名、憑證猜測／失敗降級、自動提權或未經認證的配對端點；Server／Protocol 契約不變。後續使用者已明確授權將本輪程式、測試與文件提交並推送至 origin/work。

操作簡化授權補充（2026-11-07）：使用者核准統一日常啟動入口、記住無秘密的上次設定並在開始前顯示摘要、明確查看本次實際 Capture、依 Capture 分組、搜尋命中編號直接回看／匯出前後片段、Epoch 編號及相對分鐘選取、自動產生不覆蓋的新匯出檔名。保留原 CLI 與進階入口；本次錄製不得用猜測最新目錄代替程序回報，篩選仍須保留整份完整性檢查。後續使用者已明確授權將本輪與前輪功能提交並推送至 origin/work。

工作台整合授權補充（2026-11-07）：使用者明確要求依序完成候選項目 1、3、4：一鍵選配置／錄製／啟動監看、錄製清單與明確選檔、關鍵字搜尋及時間／Epoch 區段回看匯出。重用既有 Launcher、設定驗證、SDK 與離線檢查器；只管理自己建立的程序。RX 管理沿用 configure，不新增 TX。檔案掃描／摘要／搜尋均須有界，收尾提示不等於完整雜湊驗證；不得因篩選隱藏整份完整性問題。遠端連線 Profile、完整 TUI、容量擴充不屬本輪。

離線 Epoch 摘要授權補充（2026-11-07）：使用者核准先保存驗收文件，再將逐 Epoch 統計整合進 recording-info，包含首末位移、區塊／bytes、遺失及世代切換紀錄與加總核對。重用既有離線檢查器及工作台入口，摘要記憶體／輸出須有上限，損壞或未收尾時保留完整性警告；不新增線上 API 或 UART 操作。

遠端雙路監看授權補充（2026-11-07）：使用者核准接續開發遠端工作台雙通道同時監看、來源標記、單路中斷隔離及畫面內手動重開。上限兩路，重用 SDK、既有 API／權限及有界 Writer；互動期間仍持續接收。非阻塞主控台輸入不得留下 input 工作執行緒，不新增 TX、自動重連、Gateway 程序管理或大型 TUI Framework。此補充取代第一版一次只監看一條的限制。

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
