# 專案狀態、核准清單與差異紀錄

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

更新日期：2026-11-05（Asia/Taipei）。本文件是實際進度、未決事項及驗證證據的唯一來源，不取代架構、設定或線上格式規格。

## 真實狀態

| 項目 | 狀態 | 證據與限制 |
| --- | --- | --- |
| 本機 Repository（版本庫） | 已建立 | 分支 `work`；Phase 0 提交 `33eedba`、Phase 1 提交 `496ab13`；本次啟動器變更依使用者授權提交並推送，版本以 Git 紀錄為準 |
| 初步架構審查 | 已完成審查 | 依使用者需求分析，當時無現有程式可審閱 |
| Phase 0 正式文件集合 | 已建立並完成文件檢查 | 指定 16 份 Markdown 均有實質內容；此狀態不等於使用者已核准或產品已實作 |
| Phase 0 正式核准 | 已核准，Phase 0 完成 | 2026-11-03 使用者接受全部建議答案；七份 ADR 為 Accepted |
| Gateway／Protocol／SDK／CLI 原始碼 | Implemented／Tested | Fake Serial 與 pySerial 通路可用；Win11 本機 RX 已由使用者實際執行確認，完整 Phase 1 尚未完成 |
| 單元／整合測試 | Tested | Linux／Python 3.12.14／pySerial 3.5：107 項通過、無略過，包含真 TCP、Fake Serial 與真 pySerial PTY |
| Win11／IPCAM 本機 RX | Verified（使用者執行並回報） | Gateway 成功啟動，uart.exe watch 可讀到 COM3 所接 IPCAM 的開機訊息 |
| 本機一鍵啟動器 | Verified（使用者執行並回報） | Win11 Quickstart、Ctrl+C 後重啟、重複啟動保護及 MobaXterm 占用交叉測試全部通過；另有 77 項自動化測試 |
| 初期 MobaXterm 連線 | 歷史背景 | 與本次 Gateway／CLI 成功接收的證據分開記錄 |
| SSH／Podman 遠端路徑 | Verified（使用者執行並回報） | dxan 遠端 RX、Tunnel 中斷提示及重建後手動重連／重播成功；Gateway 未受影響。長時間穩定性與資料完整性尚待驗證 |
| dxan 歷史重播與終端顯示 | Verified（使用者執行並回報） | 新版重播、顏色／換行與重新執行監控成功；未量測零遺失或所有終端序列 |
| Commit／Push（提交／推送） | 前次交接已保存 | `496ab13` 已保存前次 Phase 1 交接；本次啟動器依使用者明確授權 commit／push |

狀態語意：`Planned` 為預計開發；`In Progress` 為正在進行；`Implemented` 為已有實作；`Tested` 為已執行具體測試並有結果；`Verified` 為在聲稱的真實條件下實際驗證。不可因 Implemented 就改成 Tested，也不能以模擬測試把 Windows 硬體標為 Verified。文件可標示「已建立／已檢查」，但不讓這些詞代表產品已實作。

## 已確認方向與候選決策

使用者本次明示：Windows 單程序且獨占 COM、多 Channel 模型、Phase 1 一個實體 UART、COM3 初始目標、115200／8N1 預設、共同 SDK、Core 與 Protocol 的依賴邊界、非阻塞 Serial 協調、有界 Queue（佇列）、多 Reader（讀取者）、Exclusive Lease（獨占租約）、Fencing Token（隔離序號）、Binding（綁定）與 Epoch（世代）檢查、不自動重送 TX、明確 Gap（資料缺口）、SSH 之外仍需認證、錄製失敗不得宣稱完整。

使用者已於 2026-11-03 接受建議答案，七份 ADR 為 Accepted。核准代表採用設計基線，不代表實作或實機驗證完成。2026-11-05 已明確授權開始實作。

## 決策清單（已接受建議；現場資訊待驗證）

「阻擋」分成規格／實作、實機驗收及後續階段；不把所有未知硬體資訊都當作不能完成文件的理由。

| 編號／問題 | 架構影響 | 是否阻擋 Phase 1 | 建議答案／預設 | 理由 | 需使用者核准或提供資訊 |
| --- | --- | --- | --- | --- | --- |
| Q-01 同時 UART 上限 | 記憶體、執行緒與容量驗收 | 阻擋容量承諾，不阻擋文件 | 設定上限先 4；Phase 1 實體驗證 1、Fake（模擬）驗證至少 2 | 保留隔離模型，控制初期驗證成本 | 需核准 |
| Q-02 同時 Client 上限 | Queue、認證及公平排程 | 阻擋容量驗收 | 先 8 條 TCP 連線，含未認證連線 | 有界且足夠測多讀取者 | 需核准 |
| Q-03 最高 Baud Rate（鮑率） | 吞吐、Buffer（緩衝）與 Driver 行為 | 阻擋高於預設值的支援承諾 | Phase 1 僅驗證 115200；更高如 921600 放 Phase 2 實測 | 不將可設定等同已支援 | 需核准 |
| Q-04 Rebind／Confirm 詳細流程 | 舊 TX 失效、換線與目標確認 | 阻擋相關實作 | 採先釋放 Lease、Rebind 停止端點、換線、Confirm 建立新世代的兩步 API | 同 Adapter 換板無法可靠自動偵測 | 明確流程原則已確認；兩步契約需核准 |
| Q-05 Restart（重啟）後 Binding | 自動 RX 與 TX 授權邊界 | 阻擋啟動與復原政策 | 可先 RX，但每次重啟／裝置遺失後重新 Confirm 才能 TX | 避免沿用過期目標資訊 | 需核准 |
| Q-06 MobaXterm 是否 V1 必要 | Raw／Telnet 相容介面會擴大 Phase 1 | 阻擋 Phase 1 範圍定案 | V1 暫不納入；請提供使用模式與是否必需 | Telnet 不等於 Raw TCP（原始串流） | 需核准及提供資訊 |
| Q-07 Raw 是否雙向 | 認證、Lease 與終端相容性 | 若 V1 必要則阻擋，否則 Phase 2 | 預設關閉，未來先只讀 | 一般終端缺乏結構化認證與 Lease 操作 | 需核准 |
| Q-08 認證與授權政策 | Principal（身分）、ACL（存取規則）、Audit（稽核） | 阻擋安全實作 | 每人／工具獨立高熵 Token，明列 Channel 與 read／tx／configure 權限；禁止匿名 | SSH 不能識別所有本地 Client | 需核准；不得提供真實秘密到文件 |
| Q-09 Recording（錄製）預設 | 斷線後證據與敏感資料保存 | 阻擋錄製預設及驗收 | 預設關閉，Ring 常駐；核准保留政策後明確啟用 | 避免未確認就長期保存敏感內容；代價是長斷線可能失去歷史 | 需核准 |
| Q-10 保留期限與機密性 | 磁碟容量、權限與輪替 | 啟用錄製前阻擋 | 啟用後候選 7 天且每通道 1 GiB，先不記完整 TX；請確認 Log 是否含帳密 | 容量與年齡雙重有界，減少秘密外洩 | 需核准及提供資訊 |
| Q-11 DTR／RTS（控制線） | Reset（重設）與 Boot Mode（開機模式）風險 | 接線前提已確認，等待實機驗收 | 使用者確認僅 TX／RX／GND；DTR／RTS 未接，沿用 false | 軟體值無法保證開啟瞬間無脈衝 | 已由使用者提供資訊，未自行量測 |
| Q-12 Adapter 唯一序號 | 裝置匹配與自動重開 | 阻擋自動重連驗收 | 優先 VID/PID + 唯一序號；缺少時用明確 Port 與人工確認，自動重連預設關閉 | 同名 COM 不代表同一裝置 | 需提供實際裝置資訊 |
| Q-13 Podman Network Mode（網路模式） | Host／Container 可達性與隔離 | 阻擋正式部署驗收 | 保留現有模式，不先改 Host Networking（共用主機網路） | 避免未核准的隔離變更 | 需提供 Rootless／Rootful 及網路模式 |
| Q-14 Container 到 d3s1 SSH | 第二段 Local Forward（本地轉送）可行性 | 阻擋部署驗收 | 使用已驗證可達的 SSH 位址與受限金鑰，不假定 DNS 或特殊 Host 名稱 | 容器 Loopback 不是主機 Loopback | 需提供位址／Port 與授權方式，不提供秘密 |
| Q-15 人與 Codex 的優先權 | Lease 交接與管理權限 | 阻擋交接政策 | V1 不自動搶占；現持有者釋放或到期，Unknown 時人工處理 | 不讓工具偷偷中斷另一個操作 | 需核准 |
| Q-16 可接受的資料中斷 | Replay（重播）容量與持久化 | 阻擋完整性／保存時間承諾 | Ring 先 4 MiB；明確回報 Gap，不承諾零遺失；較長保存另啟用錄製 | 115200／8N1 約可保留 6 分鐘原始 bytes，非固定 SLA（服務水準） | 需確認可接受缺口與保留需求 |
| Q-17 Firmware／TFTP 網路與復原 | 未來升級工作流程 | 不阻擋 Phase 1，阻擋 Phase 3 | 先不做自動升級；屆時確認 UDP 路徑、板型與復原方式 | TCP SSH Forward 不等於 TFTP 通道 | Phase 3 前需提供資訊及核准 |
| Q-18 Python 支援範圍 | 依賴、CI（持續整合）與部署 | 阻擋套件基線 | 最低 3.12，候選測試 3.12／3.13；一個 Distribution（發布套件）加 Extras（選用依賴） | 控制維護範圍，不強迫多套件發布 | 需核准 |
| Q-19 V1 詳細契約與工程預設 | Hello／Auth、錯誤、序號、去重、Limits（上限）、期限 | 阻擋 Server／Client 實作 | 審閱 Protocol 與 Configuration 後核准候選契約；Lease 15 秒、5 秒更新，Frame 64 KiB 等均為建議 | 必須有單一可實作契約，但不能把新增細節冒充已核准 | 需核准 |

## 原審查差異、未定義處與處理建議

沒有現有 Source Code 可對照。以下區分原審查真正需要澄清的語意差異與當時未定細節，不把未定事項假稱為已修正的程式缺陷。

| 編號 | 原審查情況 | 本次候選處理 | 決策狀態 |
| --- | --- | --- | --- |
| C-01 TX 拒絕名稱 | 原文使用 `rejected_before_io`；本次任務使用 `rejected` | 統一候選線上狀態 `rejected`，明定沒有開始 Serial I/O；保留原因碼 | 依本次術語，隨 Q-19 審閱 |
| C-02 Sequence（序號）範圍 | 原文建議每 Channel／Epoch 的事件序號，未定義篩選事件後是否能推論缺口 | 本草案建議 `seq` 僅用於 RX Chunk（資料區塊），Offset 才用於缺口與續讀 | 這是候選細化／變更，需 Q-19 核准，未視為原決策 |
| C-03 Hello 與認證 | 原圖將兩者合併描述，未固定訊框 | 候選採 Hello 協商後獨立 Authenticate；同一總握手期限，認證前不建立 Session | 原先未定，需 Q-19 |
| C-04 重連與 TX 去重 | 原文提有限快取，未保證容量滿載或過期後的語意 | 接受前預留結果槽，保留期內不淘汰；滿則拒絕；過期／重啟查詢為 unknown，從不自動重送 | 原先未定，需 Q-19 |
| C-05 Binding 與 Epoch | 原文說明換板與 Confirm，但未固定何時開關 Handle（控制代碼）及遞增 Revision | 候選兩步 API、成功 Confirm 新邏輯 Epoch；已開 RX 可不重開 Handle；失敗不授予 TX | 原先未定，需 Q-04／Q-05／Q-11 |
| C-06 舊訂閱遇到新 Epoch | 原文允許 Session 保留，但未固定訂閱是否延續 | Session 保留，舊 Epoch 訂閱終止並通知；新訂閱明確處理世代變更 | 原先未定，需 Q-19 |
| C-07 Raw／MobaXterm 與 Phase 1 | 原審查建議延後，但使用者尚未確認是否必要 | 候選延後且先只讀，清楚保留範圍核准問題 | Q-06／Q-07 未核准 |
| C-08 建議與正式決策 | 原審查有「正式建議」措辭，不能等同使用者正式接受 | 將本次明示原則與新增細節分開；七份 ADR 都 Proposed | 已依本次指示標示，尚無 Accepted ADR |

## Phase 0 初次交付的一致性審查（歷史紀錄）

文件建立後使用既有 Python 標準函式庫進行一次性、未存檔的唯讀檢查，沒有建立產品程式、安裝套件或測試框架。檢查結果如下。

| 檢查 | 實際結果 | 證據限制 |
| --- | --- | --- |
| 文件清單與範圍 | 指定 16 份 Markdown 齊備；`.git` 外無非 Markdown 檔案或空目錄 | 未建立未來 Source Code 目錄 |
| Markdown 連結 | 71 個內部相對連結皆指向現有檔案 | 沒有外部網路連結可達性測試 |
| 編碼與排版 | UTF-8 可讀、無 BOM／CR／尾端空白；程式碼區塊成對；表格欄數一致 | 非完整 Markdown 渲染測試 |
| JSON 範例 | 22 個範例均可解析，無重複 Key；Envelope（外層）及必要範例欄位檢查通過 | 不代表 Server／Client 實作相容性已驗證 |
| Payload 與上限 | Base64 解碼、RX Offset 長度、TX bytes 與 Frame 大小通過；16 項 Hello 數值對應設定一致 | 未做吞吐或壓力測試 |
| 架構圖 | 5 個 Mermaid 區塊，其中架構文件 4 圖、部署文件 1 圖；完成圖文與依賴方向人工檢查 | 未安裝渲染器，未執行 Mermaid 圖形渲染 |
| 跨文件責任與階段 | 模組名稱、Core／SDK 邊界、Phase 1 排除範圍、Binding／TX／Epoch 文字已交叉檢查 | 尚待使用者核准候選政策 |
| ADR 與核准 | 7 份全為 Proposed，沒有擅自 Accepted；19 項 Q 編號及引用可對照 | 原則已確認不等於整份 ADR 已核准 |
| 語言 | 人類閱讀內容已人工審閱為繁體中文；常見簡體字篩查未發現候選字 | 自動字形篩查不等於完整語言判定；英文識別名稱按要求保留 |
| Git | `work`、尚無 commit；僅 16 份新 Markdown 待追蹤 | 未 stage（暫存）、commit 或 push |

交叉審查發現並已在候選規格中對齊的事項：

- D-01：初稿把非法 Base64 同時列為 Frame 與參數錯誤；已統一為已解析 Request 的 `INVALID_PARAMS`，外層格式錯誤才關閉連線。
- D-02：清理訂閱後可能找不到需通知的使用者；已明定生命週期變更前保存受影響 Session 集合，終止訂閱後仍送出變更狀態。
- D-03：Principal 設定原先允許任意 ASCII，但 Wire ID（線上識別）較嚴格；已對齊 `[A-Za-z0-9_-]`。
- D-04：已補明示 Revision／Epoch 轉移表、晚到 I/O 回報及有限生命週期等待；避免由兩端自行決定何時失效。
- D-05：已區分真正 I/O Unknown 與查詢找不到紀錄；後者不單獨封鎖 Channel。不同情境 JSON 範例不按文件順序拼成一條連線。
- D-06：移除尚無用途的 Raw 啟用設定占位；Raw 介面有核准需求後才加入設定結構。

本次檢查未發現未列出的內部矛盾。原審查的 C-01–C-08 及核准清單仍保留，不因文件一致就視為使用者已接受。產品測試、實機、正式網路及圖形渲染均未執行。

## Phase 0 核准與交接（2026-11-03）

2026-11-03 使用者明確接受 Q-01～Q-19 的建議答案，並授權更新文件、commit 與 push。Phase 0 正式完成，七份 ADR 已改為 Accepted。上方初次交付的審查紀錄保留為歷史證據；其中 Proposed／未核准／尚無 commit 的描述只代表當時狀態，以本節更新為準。

- 已接受：4 個設定通道上限、8 條 Client 連線、Phase 1 驗證 115200／8N1、兩步換板、重啟後重新 Confirm、V1 不納入 MobaXterm／Raw、獨立 Token 與權限、不自動搶占、4 MiB Ring、Python 3.12 起及 V1 契約與工程預設。
- 錄製預設關閉；啟用後採 7 天且每通道 1 GiB，不記完整 TX。
- 待現場取得：DTR／RTS 接線與有效電位、Adapter 唯一序號、Podman 權限／網路模式、Container 到 d3s1 的 SSH 位址與 Port。不得以採用建議代替實測；TFTP／韌體復原資訊延至 Phase 3。
- 尚無產品原始碼、產品測試或正式部署；本次沒有進入 Phase 1。

## Phase 1 第一批：無硬體通路（2026-11-05）

使用者已明確要求開始實作。此批目標為 Fake Serial → 通道核心 → 真 TCP Gateway → SDK／CLI；保留已核准模組邊界，不操作實體 COM，不修改 SSH／Podman。

| 項目 | 狀態 | 實際內容 |
| --- | --- | --- |
| Python Package | Implemented／Tested | Python 3.12 起、setuptools、src 結構、兩個命令入口；無執行期第三方依賴 |
| Protocol | Implemented／Tested（切片） | 有界 LF Framing、UTF-8／JSON 深度、重複 Key、Base64、Request ID、方法參數與版本 |
| Gateway | Implemented／Tested | Loopback、Hello／Auth、Principal／Channel ACL、Session 清理、心跳、分頁查詢 |
| Channel／Binding | Implemented／Tested | 獨立狀態與 RX Ring、Rebind／Confirm、新 Epoch、舊訂閱終止、並行 Label 預留 |
| RX | Implemented／Tested | 多訂閱、二進位資料、Replay 與中段 Offset、歷史淘汰 Gap、慢速接收隔離 |
| Lease／TX | Implemented／Tested | 單一 Worker、入列與派送前檢查、Fencing、到期／斷線撤銷、部分／逾時寫入 Unknown、有限去重與結果保留 |
| SDK／CLI | Implemented／Tested | 握手、認證、查詢、訂閱、Lease Context 自動更新、TX 查詢；回覆不明以 TxUncertain 保留 ID，絕不自動重送 |
| Fake Serial | Implemented／Tested | 有界 RX／寫入觀測、TX 回送、可注入延遲、逾時、短寫入與裝置故障 |
| 完整設定、錄製與稽核 | Planned | 局部 TOML 載入器以外的設定明確拒絕；尚無持續錄製、Audit 儲存與磁碟故障驗收 |
| Windows Serial／正式部署 | Planned | 未建立 pySerial Backend、未開 COM3、未驗證 USB／DTR／RTS／SSH／Podman |

### 驗證證據

- 執行環境：Linux、Python 3.12.14；虛擬環境使用既有 setuptools，離線 editable 安裝成功，未下載第三方依賴。
- 測試命令：`.venv/bin/python -m unittest discover -s tests -v`；41 項通過，含單元與真 TCP 整合。
- 故障驗證包含過期工作未寫入、寫入中不交接、Unknown 不重送、結果快取滿載拒絕、Slot 保留、斷線清理、關閉失敗、超長未結束 Frame、SDK 本地事件溢位及並行 Confirm 同名競爭。
- 已安裝的 `uart-gatewayd` 與 `uart` 在 127.0.0.1 暫時 Port 完成啟動、認證、列出、確認、訂閱、TX 回送、查詢、Rebind／新 Epoch 與正常停止。
- 本機 Socket 在一般沙箱受限制，整合測試改用允許本機 Socket 的執行權限；未略過測試、未連外部服務。
- 原 Protocol 文件 22 個 JSON 範例亦作為固定契約資料驗證。這不是完整 V1 相容性或實體零遺失證明。

### 已知界線與下一批工作

Phase 1 仍為 In Progress。此切片的對外設定使用預設容量／期限，只接受範例中的網路、通道描述與認證欄位；Fake Serial 的 RX／TX 等待有界，尚未建立真實 Driver 卡死時的生命週期等待／復原機制。SDK 目前由呼叫者明確重連與恢復 Cursor；沒有自動恢復 Lease 或 TX。

接續先加入完整設定模型及 Windows pySerial Backend，實機前確認 DTR／RTS、Adapter 序號與 COM 名稱，再測 RX、TX、占用與插拔；同階段補錄製格式、輪替、稽核與磁碟故障測試。正式 SSH／Podman 路徑依現場資訊驗證。TUI／MCP／Firmware Workflow 仍依後續階段，不提前建立目錄。

第一批交付時尚未 commit／push；現已納入本次交接保存。開始接續前先檢查 Git Status，保留現有實作與使用者變更。執行與安裝步驟見 README。

## Phase 1 第二批：Serial Backend 與 Win11 接收準備（2026-11-05）

使用者要求繼續，並明確提供 COM3 已接 IPCAM、MobaXterm 可收到資料且已關閉。後續確認只有 TX／RX／GND、DTR／RTS 未接，115200／8N1／無流控；Win11 由使用者執行 PowerShell，不是 Agent 遠端操作。

### 已實作與已測試

- pySerial 3.5 選用依賴 `[server]`；Backend 與 SDK 匯入不載入 pySerial、不開啟 COM。
- 全部 Adapter Selector 條件交集、模糊匹配拒絕、程序內端點預留、Windows 原生獨占與 POSIX exclusive；COM 名稱不當永久身分。
- Serial 設定可調，DTR／RTS 在 open 前設定；有限 read／write 等待，不額外 flush、不補送 bytes。
- 裝置枚舉、開啟錯誤分類、只讀逾時／拔除、寫入未知與保留端點；開啟失敗不關閉其他 Channel 或 TCP 管理介面。
- 生命週期等待上限及晚到結果不重新確認目標；未收斂 Driver 操作禁止交接。這不是安全強殺執行緒的保證；極端卡死仍可能需要人工處理程序。
- `examples/gateway.com3.toml`：穩定邏輯 ID `ch_uart_1`，目前端點 COM3，只授予 read；首次只接收，不發 IPCAM 指令。
- `watch --text` 安全文字顯示與增量 UTF-8；`--replay` 從目前最早可用 Offset 接上即時 RX，明確標示模式。

本次 Linux／Python 3.12.14／pySerial 3.5 共 58 項測試通過（無略過），包括 3 項真 pySerial PTY 整合。安裝後的實體模式與 CLI 已以 PTY 驗證：裝置枚舉、Channel 快照、只讀拒絕 TX、文字 RX、控制字元跳脫與正常停止。並非 Windows COM 或硬體零遺失證據。

### 交付與下一步

提供 `uart-gateway-win11-test.zip` 原始碼測試包，不含 `.git`、Linux `.venv`、私密設定或 Token。完整執行步驟見 [Win11 接收驗證](windows-quickstart.md)。第二批當時以測試包提供未提交程式；本次交接將程式、測試與文件一併保存至 work，後續可從 Git 取得。

第二批交付時等待使用者執行；後續本機 RX 已成功，詳見下方實機紀錄。正式網路、常駐錄製／稽核及完整 Phase 1 驗收仍未完成。

## Win11 本機 RX 實機結果（2026-11-05）

使用者於本次對話回報：「在 win11 上，執行後 gateway 可以啟動，且執行 uart.exe watch 也可以讀到 ipcam 開機的訊息。」

- 已驗證範圍：本版本庫的 Gateway 在 Win11 啟動，經 COM3 接收 IPCAM 資料，本機 SDK／CLI watch 顯示開機訊息。
- 證據來源：使用者在 Win11 實際執行並回報；Agent 沒有遠端操作 Win11，也沒有取得原始 Boot Log、channels JSON 或 Driver 版本。不能宣稱已完成 Log 內容分析、長時間壓力測試或零遺失驗證。
- 沿用已確認條件：TX／RX／GND，DTR／RTS 未接，115200／8N1／無流控；測試設定只授予 read。
- 尚未驗證：實體 TX、USB 拔除／重插、Windows Port 占用錯誤、實體 Lease 交接，以及 Linux Container／SSH Tunnel 的端到端路徑。

下一步保持現有 Gateway 執行，確認目前 Windows 到 d3s1 的 SSH 連線與 Forward 命令，再將 Container SDK／CLI 接到同一個只讀 Channel。Gateway 仍僅綁定 127.0.0.1:18081，不因遠端整合改成公開 Listener。Q-13／Q-14 的網路資訊仍待提供；本次未修改 SSH／Podman 或發送 UART 指令。

## 暫停與下次接續（2026-11-05，歷史紀錄）

使用者要求中斷進度，明日再繼續，並明確授權 commit 與 push。本次保存 Phase 1 第一、二批程式、58 項已通過的測試、範例與文件；不繼續實作或修改部署。Win11 COM3 本機 RX 已由使用者實際執行確認。

下次從以下位置接續：

1. 取得 origin/work 最新提交，先檢查本地變更並保留；閱讀 AGENTS、README 與本文件。Win11 已成功的 RX 流程不需從架構審查重做。
2. 取得目前 Windows 到 d3s1 的 SSH 命令及既有 -R／-L 參數，確認 Q-13／Q-14 的 Container 網路與 SSH 可達資訊；不要提供密碼、Token 或私鑰內容到對話或版本庫。
3. 以同一只讀 Channel 驗證 Linux Container SDK／CLI 經 SSH Tunnel 接收資料。Gateway 維持 Windows Loopback 127.0.0.1:18081，不公開 Listener。
4. 若 Gateway 在休息期間已關閉，依 Win11 接收步驟正常重啟，並讓 Client 使用與新 Gateway 相同的認證資料；不得假設舊程序、Token、Session 或 Epoch 仍存在。

尚未完成：實體 TX／插拔、遠端 Tunnel、常駐錄製／稽核及完整 Phase 1 驗收。本次不重跑未變更程式的測試；沿用最近一次 58 項通過與 Win11 使用者回報，另檢查提交範圍、文件連結及空白格式。

## Windows 一鍵啟動器（2026-11-05）

使用者新增任務，明確要求先完成設計審查，再實作一鍵啟動、模擬測試與更新 Quickstart；因此從前述暫停狀態恢復此範圍工作。比較 CMD、PowerShell 與 Python 後，採薄 CMD 入口加 Python 程序管理，理由與界線見 [設計審查](local-launcher.md)。沒有修改 V1 API Contract（介面契約）。

### 已實作

- `run-com3.cmd`：使用專案 `.venv`，開啟獨立 Python 監控視窗，失敗時保留繁體中文原因。
- `uart local --port COM3 --read-only --watch`：固定 115200／8N1／無流控與既有 DTR／RTS false；重用 Gateway、設定載入器、SDK 與 watch 文字顯示，不呼叫 Confirm、Lease 或 TX。
- 每次產生高熵臨時 Token，僅在記憶體及本次子程序環境使用；臨時 TOML 只有秘密參照，命令列不帶 Token，父程序環境不被修改。
- 每端點程序鎖、既有 Listener 檢查、SDK 認證及 Channel 就緒查詢；占用／缺少端點／逾時／提早退出均失敗，不以固定 Sleep 當作成功。
- Gateway 先保留 TCP Socket 再開啟 Serial。以本次專屬 stdin 管道 EOF 通知子程序正常關閉；逾時才停止自己持有的子程序，不掃描或終止其他 Gateway。

### 自動化證據與限制

- `.venv/bin/python -m unittest discover -s tests -v`：Linux／Python 3.12.14／pySerial 3.5，77 項通過、無略過。
- 新增 19 項測試：9 項啟動器單元、9 項真子程序／PTY 整合及 1 項錯誤視窗停留測試；另強化既有測試，確認 TCP Port 已占用時不開啟 Serial。
- 覆蓋跨程序同端點鎖、臨時認證不進檔案／參數、未就緒重試、只讀權限與 UART 設定核對、二進位 RX、占用與缺少端點、重複啟動、取消／Ctrl+C、正常回收、失敗回收及立即重啟。占用測試確認原 Gateway 仍 ready。
- Windows 的 CMD 雙擊、`msvcrt` 實際鎖定、主控台 Ctrl+C、MobaXterm 占用與實體 COM3 均未由 Agent 執行。先前手動 Win11 RX 成功不代表新啟動器已實機驗證。

### 下一步

使用者已回報 [Windows Quickstart](windows-quickstart.md) 可用，並明確確認 Ctrl+C 後重新啟動、重複啟動保護及 MobaXterm 占用交叉測試三項全部通過。上述範圍更新為 Verified（使用者執行並回報），不是 Agent 直接操作 Windows，也不代表長時間零遺失或控制線量測完成。下一步接 Linux／SSH Tunnel；5～6 UART、多 Channel 啟動、互動選 Port、TUI、常駐錄製與實體 TX 均不在此次範圍。使用者已明確授權將此批程式、測試與實機結果文件 commit／push 至 origin/work。此次僅更新驗證紀錄，沿用前次 77 項全數通過的自動化結果，不重跑未變更的程式。


## d3s1 Container 遠端 RX 實機結果

使用者於本次對話回報：「在 d3s1 的 container 有收到 ipcam 透過 uart 傳輸的資訊」。此結果來自使用者現場操作，不是 Agent 直接連線或取得原始 UART Log。

- Windows 以 `ssh victor.hu-vo@d3s1` 的既有目的地建立反向轉送；d3s1 的 `ss` 輸出確認 `127.0.0.1:18081` 接聽。
- `dxan` 的 NetworkMode 為 `host`，不需要第二段 Container SSH 轉送；使用者已確認容器連到 `127.0.0.1:18081` 的 TCP 測試成功。
- 容器使用 Python 3.14.4，安裝本版本庫 CLI 後，以既有只讀 Gateway、共用認證與 watch 流程接收 IPCAM UART 資訊成功。Python 3.14 的這次操作成功不代表完整自動化相容性矩陣已通過。
- 已驗證路徑：dxan SDK／CLI → d3s1 Loopback → SSH 反向轉送 → Windows Loopback Gateway → COM3／IPCAM。維持只讀，未宣稱實體 TX 通過。
- 尚未驗證：Tunnel 中斷／重建後重新認證與訂閱、Replay／Gap 邊界、長時間穩定性與零遺失。未取得原始 Log、資料量或測試時長。

下一步先測停止及重建 SSH Tunnel，確認 CLI 能看見連線失敗，重建後以新 CLI 連線恢復只讀 RX。Gateway 保持執行，不自動重送 UART 指令；再安排長時間接收與多 UART 需求。此次僅更新文件，未重跑程式測試，也未 commit／push。


## 遠端歷史重播 SDK 佇列溢位修正

使用者尚未執行 Tunnel 中斷測試；在正常連線執行 `watch --duration 60 --replay --text` 時，訂閱快照的重播尾端為 58616，顯示少量歷史文字後出現 `LocalDataLoss：SDK 事件佇列已滿，資料連續性中斷。`。UART 內容中的系統關機訊息不是 d3s1 關機證據。先前遠端 RX 成功仍成立，但不能因此宣稱歷史重播與斷線恢復已通過。

- 以真 TCP、Fake Serial 的 400 個 146-byte 歷史區塊及每次 write 延遲 1 ms 的顯示器重現同一個 LocalDataLoss；這是模擬近似條件，未取得使用者原始 Log。
- SDK 收到事件後讓出執行機會；CLI 文字顯示改用有界批次，約 20 ms 或 16 Ki 字元刷新，保留增量解碼、安全控制字元顯示、模式切換與最終輸出。未改 Wire API、事件佇列容量或溢位失敗語意。
- 修正後回歸測試核對整段 58400 bytes 的文字輸出完整一致；Linux／Python 3.12 完整 78 項測試通過。真正慢到超出有界容量的消費者仍應明確失敗，不保證任意慢速終端可無限接收。
- 這是 Client／CLI 修正，Windows Gateway 不需因此重啟；dxan 的 Python 3.14 及原始 UART 重播需更新後重測。Tunnel 中斷情境仍未開始。

本次程式、測試與文件尚未 commit／push。提供只含 Client／CLI 與回歸測試的修補檔供現場套用；未將 Token 或 UART 原始 Log 納入。


### 重播修正複查與補強

使用者要求再次檢查修正。新增測試在初版重播修正上實際重現以下問題，並完成修正：

- 批次刷新 await 後才清空，取消時 finally 會再次送出相同文字；改為 await 前移出批次，不重試不明的部分輸出。
- 取消 to_thread 不代表底層 stdout.write 已結束；改為單次工作完成 write／flush，取消時等該工作收斂，避免清理時交錯寫入。
- 輸出失敗會跳過取消訂閱，或清理例外蓋掉原始 LocalDataLoss；現在仍嘗試清理，保留第一個錯誤，後續錯誤以例外附註保留。
- 廣泛捕捉 TimeoutError 會把 SDK 心跳逾時當成正常結束；現在只接受自身觀測期限／刷新期限到期，SDK 逾時向上回報。

驗證：Linux／Python 3.12.14／pySerial 3.5，完整 87 項測試通過、無略過。新增取消／期限中斷、輸出失敗、SDK 逾時、清理錯誤保留、部分行刷新、UTF-8 跨區塊與模式切換測試；另以真 TCP 確認完全不消費的 Client 仍觸發事件數量上限，其他 Client 可繼續操作。保留原有位元組上限測試。

新版修補檔包含 Client／CLI 及兩個測試檔，提供以 `6aa8588` 為基準的完整 v2 與已套用初版後使用的增量版本，兩者均在隔離目錄檢查套用結果。20 ms 是刷新目標，實際延遲受排程與 stdout 影響；OS 阻塞輸出時取消仍會等待。未宣稱 dxan Python 3.14 或實體 UART 重測成功，未執行 Tunnel 中斷情境。此次尚未 commit／push。


## 歷史重播與終端顯示合併版本

使用者要求先完成「重播修正」與「基本終端顯示」，再一次現場測試。保留先前重播及取消／錯誤處理修正，新增 `watch --terminal` 與 `local --terminal`，不改 Gateway、V1 API Contract、認證或 UART 安全設定。原 `--text` 及雙擊入口仍保持控制字元跳脫，終端模式為明確選項，且在連線前要求 stdout 為 TTY。

- `text_renderer.py` 是純文字增量過濾器：允許 CR／LF／BS／TAB 與重設、粗體、前景／背景 SGR 顏色；過濾其他 CSI、OSC、DCS、剪貼簿、標題與裝置查詢。
- 支援控制序列跨區塊；CSI 參數暫存最多 128 字元，控制字串不累積內容。缺口、訂閱結束與關閉時不把殘留序列接到新資料，並重設顏色；不補送任何 UART 指令。
- Linux／Python 3.12.14／pySerial 3.5 完整 101 項測試通過、無略過。兩種文字模式都通過 58400 bytes 小區塊重播與慢速輸出測試，原有 SDK 事件數量／位元組上限仍明確告警。
- 新增 7 項過濾器單元、5 項 CLI 邊界及 2 項真 CLI／主控台 PTY 測試，包含顏色與進度符號、每個切割點、UTF-8、未知／超長序列、TTY 要求、取消後恢復顏色與缺口邊界。PTY 檢查控制位元組，未觀察使用者的實際終端畫面。

提供完整 `uart-gateway-rx-terminal-test.zip` 快照，取代現場處理多版修補檔；以新目錄與獨立環境安裝，不覆蓋現有程式或認證。使用者後續明確要求 commit／push，改以 origin/work 供 dxan 直接拉取，測試包保留為替代方式。操作見 [Container 合併測試](remote-rx-test.md)。此版狀態是 Implemented／自動化 Tested；dxan Python 3.14、IPCAM 原始歷史與真實終端顯示仍待使用者驗證，Tunnel 中斷測試尚未開始。本次依使用者授權提交與推送，實際版本以 Git 紀錄為準；程式未再變更，沿用上述 101 項通過的結果。


## 現場重播再次溢位：分離事件消費與畫面輸出

使用者回報顏色與換行正常，但 `--replay --terminal` 仍出現 `LocalDataLoss：SDK 事件佇列已滿`；訂閱快照 `replay_until_offset` 為 57982、`earliest_offset` 為 0。顏色／換行記為 Verified（使用者執行並回報），不能因此把重播、進度符號、Ctrl+C 或 Tunnel 中斷一併標成已通過。

先前每次 write 延遲 1 ms 的測試不足：將每次終端 write 延遲改為 200 ms，以 58400 bytes、400 個區塊重播，實際重現 SDK LocalDataLoss。這是模擬造成同類故障的條件，沒有量測現場終端實際延遲。問題是 CLI await 畫面寫入時停止取 SDK 事件，原本的 yield 與批次輸出仍無法承接較長的短暫停頓。

本次修正：

- 事件消費與有序畫面 Writer 分成兩個工作，stdout／stderr 寫入不直接阻塞事件消費。顯示佇列上限為 256 項與 262144 UTF-8 bytes，包括正在寫入的批次；使用緊湊文字承接暫時停頓，不是調大 SDK 事件容量。
- SDK 的事件數量與位元組上限、Wire API、Gateway、Token、DTR／RTS 及只讀授權均不改動；不新增 UART TX。
- 顯示持續跟不上時明確回報「CLI 顯示佇列已滿」，不丟棄資料後假裝完整。Writer 失敗會喚醒等待 UART 的監控；同時接收失敗時保留接收原因並附註輸出錯誤。
- 關閉先取消訂閱，再等待已排入的畫面輸出完成；Writer 收尾恢復顏色，包含 Producer 因容量不足失敗的情境。OS 若阻塞輸出，仍不能宣稱能安全強殺該 I/O。

驗證：Linux／Python 3.12.14／pySerial 3.5，完整 107 項測試通過、無略過。新增的 6 項測試涵蓋兩種模式的 200 ms 輸出停頓、畫面停住時仍完整消費歷史與完成 RPC、UTF-8／寫入中容量、項目上限、無新 UART 時的 Writer 失敗通知，以及雙工作同時失敗的原始錯誤保留。原有 SDK 溢位測試仍通過。

依使用者先前要求持續採 Git 交付，本輪提交並推送 origin/work，供 dxan 拉取後重測；不需重啟 Windows Gateway 或更換 Token。交付時現場重播修正狀態為 Implemented／自動化 Tested，後續實機回報見下段；Tunnel 中斷測試仍未開始。

## 新版遠端重播成功與下一步

在提供 `c90a0b9` 的拉取與 `watch --duration 60 --replay --terminal` 重測步驟後，使用者回覆「成功」。新版遠端歷史重播記為 Verified（使用者執行並回報）；沿用先前已確認的顏色與換行成果。這不是 Agent 直接執行，也不代表已量測零遺失或完成全部 Python 3.14 相容性測試。

下一步先測遠端 CLI 按 Ctrl+C 後能重新監控，再於保持 Windows Gateway 執行的情況下，停止及重建 Windows 的反向 SSH Tunnel，確認舊 CLI 顯示連線失敗、手動重開 CLI 可恢復 RX。Gateway 與 Token 維持不變；不自動重連、不傳 UART 指令。長時間接收、多 UART 與實體 TX 尚未驗收。

本次只更新使用者回報與現場測試步驟，沿用前次 107 項自動化通過結果，未重跑未變更的程式；文件變更尚未 commit／push。

## 遠端再次監控與 Tunnel 中斷恢復結果

使用者回報已完成前述測試，Container 重新執行 replay 可看到相同畫面；停止 SSH Tunnel 後 Container 顯示斷線，Gateway 無異動；Tunnel 重連後 Container 可重新連線，replay 仍可顯示相同畫面，並確認測試成功。

上述再次監控、Tunnel 中斷回報、Gateway 保持執行，以及 Tunnel 重建後手動重連／重播記為 Verified（使用者執行並回報）。這是目前保留歷史可再次讀取的證據，未取得逐 byte 計數、Offset 對照或長時間測試數據，不據此宣稱中斷期間零遺失或已完整錄製。CLI 仍採手動重新連線，沒有新增自動重連或 TX。

下一步建議先做 30 分鐘的持續只讀接收，記錄是否有新資料、錯誤或非預期退出，再安排長時間穩定性與可計數來源的完整性驗證。多 UART、互動選 Port、TUI、實體 TX 與完整 Phase 1 驗收仍未完成。

本次僅更新實機結果與文件，程式維持 `c90a0b9`，沿用最近 107 項自動化測試通過的證據；文件納入下述交接保存。

## 居家接續交接（2026-11-05，Asia/Taipei）

使用者目前在家，無法連到公司的 d3s1，明確要求先 commit／push。此次保存遠端 RX、歷史重播、顏色／換行、再次監控及 Tunnel 中斷後手動恢復的實機回報，並同步部署與測試文件。只改文件，檢查內部連結與空白格式；未重跑未變更的程式測試。

30 分鐘持續接收尚未執行，留待可連回公司環境時驗證；不將目前無法連線視為 Gateway 故障。居家可接續的建議是以 Mock 裝置清單與 Fake Serial 開發 COM Port 列出／選擇，仍維持單 Channel 與只讀；此功能尚未開始實作，需依後續任務進行。此次依使用者授權提交並推送 origin/work，實際版本以 Git 紀錄為準。
