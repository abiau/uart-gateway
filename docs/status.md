# 專案狀態、核准清單與差異紀錄

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

更新日期：2026-11-07（Asia/Taipei）。本文件是實際進度、未決事項及驗證證據的唯一來源，不取代架構、設定或線上格式規格。

## 真實狀態

| 項目 | 狀態 | 證據與限制 |
| --- | --- | --- |
| d3s1／dxan 遠端持續接收 30 分鐘 | Verified（使用者執行並回報） | IPCAM 每秒輸出 ps 資訊，dxan 資料正常；未量測逐 byte 完整性 |
| Win11 錄製文字匯出與查看 | Verified（使用者執行並回報） | `262f702` 後回報可看到文字、此測項 OK；不據此推論零遺失、異常路徑或全部錄製情境通過 |
| 本機 Repository（版本庫） | 已建立 | 分支 `work`；Phase 0 提交 `33eedba`、Phase 1 提交 `496ab13`；本次啟動器變更依使用者授權提交並推送，版本以 Git 紀錄為準 |
| 初步架構審查 | 已完成審查 | 依使用者需求分析，當時無現有程式可審閱 |
| Phase 0 正式文件集合 | 已建立並完成文件檢查 | 指定 16 份 Markdown 均有實質內容；此狀態不等於使用者已核准或產品已實作 |
| Phase 0 正式核准 | 已核准，Phase 0 完成 | 2026-11-03 使用者接受全部建議答案；七份 ADR 為 Accepted |
| Gateway／Protocol／SDK／CLI 原始碼 | Implemented／Tested | Fake Serial 與 pySerial 通路可用；Win11 本機 RX 已由使用者實際執行確認，完整 Phase 1 尚未完成 |
| 單元／整合測試 | Tested | Linux／Python 3.12.14／pySerial 3.5：304 項通過、無略過，包含真 TCP、Fake Serial、真 pySerial PTY、錄製／回看、單雙路工作台、長歷史重播與離線 Epoch 摘要 |
| Win11／IPCAM 本機 RX | Verified（使用者執行並回報） | Gateway 成功啟動，uart.exe watch 可讀到 COM3 所接 IPCAM 的開機訊息 |
| 本機一鍵啟動器 | Verified（使用者執行並回報） | Win11 Quickstart、Ctrl+C 後重啟、重複啟動保護及 MobaXterm 占用交叉測試全部通過；另有 77 項自動化測試 |
| 本機端點列出／選擇 | Implemented／自動化 Tested | `uart ports`、`local --select` 與 `run-uart.cmd`；Mock／PTY 通過，居家實體接收與終端顏色已回報，選單邊界待測 |
| USB 遺失後回到選單並停留 | Verified（使用者執行並回報） | `34842cd` 後使用者回報正常；不推論所有 EOF 情境或重新插回接收皆通過 |
| 兩條只讀 UART 啟動與配置 | Verified（Win11 CLI 錄製／拔除隔離，使用者回報） | 雙路選擇錄製、一路遺失另一路持續及兩路正常收尾已回報；Profile 重用／重編 COM、雙擊與整組占用清理仍待個別驗證 |
| Win11 雙 UART 故障與恢復隔離 | Verified（使用者執行並回報） | COM3／COM4 各接一台持續輸出的 IPCAM；COM3 拔除與手動重開期間，COM4 未曾中斷。反向故障、雙路錄製完整性及 dxan 新操作不據此推論通過 |
| Gateway RX 錄製及離線檢查 | Verified（一般流程，使用者回報） | 原第 2～3 項本機錄製與離線操作通過；故障／容量等情境仍以自動化證據及個別實測為準 |
| 雙路錄製單路遺失隔離 | Verified（Win11，使用者提供 Log／檢查摘要） | COM3 拔除後正常收尾且連續性未知；COM4 持續接收、正常收尾且對已觀測資料無已知缺口。雙路皆健康的一輪、長時間／輪替／磁碟錯誤與逐 byte 完整性不據此推論 |
| 錄製中單路手動 RX 重開 | Verified（Win11，使用者提供快照／檢查／Epoch 統計） | COM3 新舊兩個 Epoch 各自從位移 0 記錄且均有資料；COM4 Epoch 不變。兩路正常收尾，COM3 保留連續性未知、COM4 無已知缺口；統計與 recording-info 一致 |
| recording-info 原生 Epoch 摘要 | Implemented／自動化 Tested | 每世代首末位移、區塊／bytes、狀態／切換紀錄與總計核對；跨分段、未知／損壞、輸出上限及真 CLI 通過。Win11 新顯示仍待使用者用既有錄製驗證 |
| 本機只讀互動式工作台 | Verified（一般流程，使用者回報） | 原第 1～3 項更新／枚舉、監看錄製清理返回及離線檔案操作通過；雙擊與 USB 完整恢復不另推論 |
| 遠端只讀監看工作台 | Verified（Win11 同機一般流程，使用者回報） | 原第 4 項通過；新版人工 RX 重開另列，dxan 新選單及實體恢復仍待測 |
| 遠端雙路同時監看工作區 | Verified（Win11 一般流程，使用者回報） | 使用者已實際操作合併畫面與 O／Y；03fbc96 長歷史重播修正後回報測試正常。不推論全部鍵盤／錯誤子情境通過，dxan 新操作仍待測 |
| Gateway 狀態日誌與單通道手動 RX 重開 | Verified（Win11，使用者回報） | 2026-11-07 回報日誌、USB 拔插、O／Y 恢復、未插回拒絕、MobaXterm 占用與釋放後恢復通過；後續亦回報雙實體單路恢復隔離通過，dxan 新操作仍待測 |
| 初期 MobaXterm 連線 | 歷史背景 | 與本次 Gateway／CLI 成功接收的證據分開記錄 |
| SSH／Podman 遠端路徑 | Verified（使用者執行並回報） | dxan 遠端 RX、Tunnel 中斷提示及重建後手動重連／重播成功；Gateway 未受影響。持續輸出 30 分鐘已回報正常；逐 byte 完整性仍待量測 |
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


## 居家開發：本機 COM Port 列出與選擇（2026-11-05，Asia/Taipei）

使用者在保存交接後明確要求開始實作下一步。本輪完成本機端點列出與單端點互動選擇，沿用 Gateway、SDK、只讀權限、UART 安全設定、程序鎖與清理；沒有新增 TX、遠端枚舉 API、多 Channel 啟動或 TUI Framework。

- `uart ports` 顯示本機 Port、描述、序號、VID／PID 與位置，不開啟 Serial、不啟動 Gateway、不需要 Token；空清單是正常結果。
- `uart local --select` 與 `--port` 互斥；新增 `run-uart.cmd`。原固定 COM3 入口及未指定端點時的 COM3 預設保留。
- 只有明確輸入編號才啟動，即使只有一個裝置也不自動開啟。支援重新整理、無效輸入、Enter／Q／EOF／Ctrl+C 取消；互動輸入在 asyncio 啟動之前，不留下 input 執行緒。
- 選定後重驗端點唯一性與身分；消失或改變時重新選擇，不沿用舊編號。可用的完整 VID／PID、序號與位置條件進入既有 Selector，再於開啟及 Ready 階段核對。描述是本機 UI Metadata，不改 Core 或 Wire Model。

驗證：Linux／Python 3.12.14／pySerial 3.5，完整 132 項通過、無略過。新增 18 項枚舉／選單單元與 CLI 測試、4 項啟動器身分／設定測試及 3 項程序／PTY 整合。覆蓋快照變更、重複別名、無裝置、取消、選擇後真子程序只讀 RX、端點消失清理與真 CLI 選單 SIGINT；既有占用測試亦覆蓋選擇端點。另在允許本機事件迴圈喚醒的執行環境，以真 `uart ports` 確認無硬體時正常顯示空清單，未開 COM。

狀態為 Implemented／自動化 Tested，尚未在 Win11 執行新 CMD 選單或以實體 USB-UART 測試選擇／插拔。沒有唯一序號時不能保證同名 COM 一定是同一 Adapter；也不能識別同 Adapter 換接另一塊板子。30 分鐘接收、公司 d3s1 及實體 IPCAM 驗證維持待測。操作與新驗收清單見 Windows Quickstart。完成後沿用使用者指定的 Git 交付方式保存至 origin/work，實際版本以 Git 紀錄為準。


## 居家 USB-UART／IPCAM 接收與終端顏色結果

使用者在新選單的現場測試脈絡中回報：「家裡有 usb-uart 與 ipcam，剛剛測試可以讀到資料。--terminal 也可正確顯示顏色。」居家本機實體 RX 與終端顏色記為 Verified（使用者執行並回報），不是 Agent 直接操作或取得完整 Log。未另外回報 COM 名稱、裝置型號、測試時長或所有選單步驟，不將本次成功推論為取消、重新整理、占用與插拔情境也已通過。

家裡已有資料來源，接下來可先做 30 分鐘本機持續只讀接收；公司 d3s1 的遠端長時間路徑仍待可連線時驗證。看到資料不代表已量測零遺失。程式仍為 `d9bb714`，本次僅更新文件，沿用最近 132 項自動化通過證據，不重跑未變更程式；文件尚未 commit／push。


## USB 拔插與 IPCAM 重新上電回報

使用者回報 USB 拔插時程式中斷；IPCAM 電源重新上電時仍持續顯示資料；30 分鐘持續接收尚未測試，之後另找時間進行。這是使用者現場觀察，不是 Agent 直接執行。

USB 拔除後停止監控符合目前 Channel 遺失時終止訂閱、不自動重連的政策；但未取得當時退出碼或錯誤輸出，不能據此宣稱已驗證完整正常清理、沒有 Traceback，或重新插入後可自動恢復。IPCAM 重上電後仍可顯示資料記為 Verified（使用者執行並回報），不推論特定控制線脈衝或零遺失。

下一個建議切片是選單模式的中斷處理：清楚提示裝置已失去、釋放本次 Gateway，再由使用者選擇重新列出／選擇端點或離開，不自動沿用同名 COM。這僅為後續建議，尚未實作；既有腳本模式退出語意仍需保留。長時間測試繼續待測。本次僅更新文件，程式及最近 132 項自動化測試證據不變，文件尚未 commit／push。


## 互動選單中斷後手動恢復（2026-11-05，Asia/Taipei）

使用者要求進行下一步並詢問是否要做 Win11 Python Client。本輪沿用現有 SDK／CLI 與原模組化規劃，完成先前建議的手動恢復切片，不另建 Client、GUI 或 TUI Framework。

- `local --select` 於裝置遺失／訂閱終止、已知啟動或連線錯誤後，等待本次 Gateway 清理，再重新枚舉與顯示選單；只有新的明確選擇才啟動，不沿用舊編號或裝置條件。
- 裝置遺失有繁體中文提示；非裝置錯誤保留原錯誤原因，不偽裝成 USB 拔除。Ctrl+C／取消、正常 `--duration` 及參數錯誤離開流程；固定端點與遠端 watch 的退出語意保留。
- 子程序無法確認清理時回報 `LOCAL_CLEANUP_FAILED` 並停止，不回選單啟動另一個 Gateway。
- 新啟動建立新的 Token、Instance、Epoch 與 Ring；畫面明示舊歷史不保留。無 Wire API 變更，也沒有 Confirm、Lease、TX 或自動重連。

驗證：Linux／Python 3.12.14／pySerial 3.5 完整 141 項通過、無略過，未出現 RuntimeWarning。新增 6 項選單恢復／退出測試、1 項中文裝置遺失提示、1 項清理失敗分類及 1 項真程序／PTY 完整恢復測試。後者實際收到第一個虛擬端點資料，模擬拔除，第二次選擇前確認舊子程序結束、臨時檔刪除與鎖釋放，再選另一端點接收；Token、Instance 與 Epoch 皆不同，兩次皆只授權 read。原非同步啟動器測試亦完整執行。

本輪狀態為 Implemented／自動化 Tested；Win11 實體插拔後回選單與重新接收仍待使用者測試。先前居家接收、終端顏色及 IPCAM 重新上電的成功紀錄保留，30 分鐘接收繼續待測。操作見 Windows Quickstart。依使用者既有 Git 交付方式保存至 origin/work，實際版本以 Git 紀錄為準。


## 恢復選單空白 Enter 與 EOF 區分（2026-11-05，Asia/Taipei）

使用者提供 Win11 實機輸出：COM3 Ready 後收到 `subscription_state` 的 `device_lost`，出現中文遺失提示、本次 Gateway 清理及返回選單的訊息；COM3 已不在新清單。之後顯示空白輸入與「已取消」，程式退出。這份紀錄可確認已返回選單，不能單憑舊版統一取消訊息分辨空白 Enter 或 EOF，也不據此宣稱 USB 重新插入後接收已通過。

本輪改善輸入語意：初次選單仍允許 Enter 取消；裝置／連線錯誤後的恢復選單 Enter 改為重新整理，不退出、不自動選端點。Q／Ctrl+C 可離開；若實際是 EOF，明確顯示輸入來源已結束後退出，避免默默當成使用者取消或無限重試輸入。

驗證：Linux／Python 3.12.14／pySerial 3.5 完整 144 項通過、無略過、無 RuntimeWarning。新增 3 項空白輸入、明確選擇及 EOF 訊息測試，並將真 Gateway／PTY 完整恢復測試加入「返回選單先輸入空白，再輸入編號」；空白時原 Gateway 已結束且沒有第二個 Gateway，明確選擇後才建立新資料流。SDK、Core、Serial Backend 與 Wire Contract 不變。

此修正仍待 Win11 重測。若退出時出現 EOF 新提示，需再確認該終端的輸入狀態；沒有假定已證明原本空白輸入的來源。沿用使用者指定的 Git 交付方式保存至 origin/work，實際版本以 Git 紀錄為準。


## Win11 恢復選單停留結果

使用者於 `34842cd` 更新後回報可正常運作，拔除 USB-UART 後仍出現更新後的選單。此項「USB 遺失後返回並停留於恢復選單」記為 Verified（使用者執行並回報）；未將此訊息擴大解讀為 30 分鐘持續接收、全部 EOF 情境或多 UART 驗收通過。

下一個建議方向是多 UART 的配置與操作設計：維持單一 Gateway／多 Channel，先以兩個 Mock 端點驗證同時接收及故障隔離，再逐步向使用者目標的 5～6 個 UART 做容量評估。現有載入器上限仍為 4，尚未調整；不以新增多個獨立 Gateway 取代既有多 Channel 架構，也不預先建立完整 TUI。此為建議，尚未實作。

本次僅更新使用者實機回報，程式仍為 `34842cd`，沿用最近 144 項自動化通過證據；文件變更尚未 commit／push。

## 兩條只讀 UART 啟動、配置與隔離（2026-11-05，Asia/Taipei）

使用者接受下一步建議並明確要求實作。本輪先完成 `local-launcher.md` 的設計審查，再沿用既有 Gateway、SDK、設定載入器與 `watch_client` 完成兩條切片：

- `local --select --count 2` 依序選擇不同端點並命名；`run-dual-uart.cmd` 為薄雙擊入口。單端點命令及選單恢復流程保留。
- `--save-profile` 保存新配置，`--profile` 重用名稱與裝置條件；不包含 Token、權限或確認資格。有序號且其他條件唯一符合時可解析目前 COM；模糊、不符或兩條解析到同一端點則拒絕。既有檔案不覆蓋。
- 取得全部端點鎖後只建立一個 Gateway；兩條全部 Ready 且權限／UART 設定正確才監控。啟動失敗清理整組，既有 Listener 或占用端點的程序不受影響。
- 各 Channel 使用一條 SDK Session，共用原有有界畫面 Writer。每批／每行有來源標記；UTF-8 與顏色狀態分開。多通道 CR／BS 跳脫顯示，避免改寫其他來源，原單通道 `--terminal` 不變。
- 運行中單路遺失或 SDK 接收失敗，另一條繼續；全部停止才清理並回選單。共用輸出失敗或 Gateway 整體退出則結束整組，Ctrl+C 清理所有自有連線及唯一子程序。沒有自動重連、TX、Confirm、Lease 或控制線新增操作。

驗證：Linux／Python 3.12.14／pySerial 3.5，完整 **163 項通過、無略過、無 RuntimeWarning**，執行 `python -W error::RuntimeWarning -m unittest discover -s tests -v`。新增 11 項單元與 8 項真程序／PTY 整合測試：配置與選單界限、COM 重編／模糊／重複、來源與顏色隔離、單 SDK 失敗／共用 Writer 失敗、兩路 RX、單路拔除後另一條收到新資料且 Epoch 不變、第二條占用撤銷、重複啟動、部分鎖取得失敗後釋放、既有 Listener 保護、取消與真 CLI SIGINT，以及兩路各 56400 bytes 重播遇到 200 ms 顯示停頓。完整測試沒有操作 Windows 實體 COM。

另檢查 95 個文件相對連結、Markdown 程式區塊、CLI 繁體中文說明、CMD CRLF 與 Git 空白格式。本機配置上限兩條，原 Server 載入器與 Gateway 建構器四條上限維持；五至六條仍需容量評估。Win11 雙 USB-UART、CMD 雙擊、實體 COM 重編與 30 分鐘接收皆待測，不宣稱零遺失或完整 Phase 1 已完成。前次單通道實機成功紀錄保留。

本輪狀態為 Implemented／自動化 Tested，並納入前次尚未提交的 Win11 選單停留回報。依使用者既有 Git 交付方式保存至 `origin/work`，實際提交以 Git 紀錄為準；操作與待執行驗收見 Windows Quickstart。

## 單 UART 可接續的 RX 錄製（2026-11-05，Asia/Taipei）

使用者表示手邊沒有兩個 UART，雙實體驗證需延後到公司；此項維持待測，不視為失敗或成功。使用者接續核准只讀 RX 錄製。本輪先完成 `recording.md` 的檔案格式與生命週期設計，再實作 Gateway 端錄製，不依賴 Client 存活、顯示模式或 Replay。

- `uart local --record-dir 路徑` 明確啟用；手動 Gateway 可使用 `[recording]`。預設不錄製，不把錄製選項或 Token 寫入持久端點 Profile。
- 每次／每通道新建 Capture，JSONL 保存原始 bytes（Base64）、裝置／UART／Binding 快照、Epoch、位移與序號，記錄裝置遺失及生命週期。Replay 不重複寫入。
- 每通道佇列 1024 項／預設 1 MiB，包括寫入中資料；預設每段 64 MiB、每次每通道 1 GiB，達上限停止該錄製並告警，不覆蓋舊檔。跨 Capture 保留與 Audit 尚未實作。
- 磁碟 I/O 在工作執行緒執行，溢位／磁碟錯誤以既有 Snapshot.recording 與 recording_state、中文診斷回報，RX 與其他通道繼續。裝置遺失為 unknown，已知缺口／寫檔失敗為 incomplete；不宣稱 UART 前段零遺失。
- 分段有 SHA-256 與最後收尾標記；`uart recording-info <Channel目錄>` 離線檢查，不需要 Token、不連線 UART。正常結束等待排空、flush／fsync；沒有最終 Footer 或檔案損壞不得標為無已知缺口，已知外部錄製失敗訊息仍優先於離線檢查。
- 故障測試發現 Python 3.12 在仍有 Client 時，先等待 Listener.wait_closed 可能卡住清理；已改為先停止接聽、清理 Session／Handler，再等待 Listener，最後停止 Channel 並收尾 Recorder。新增回歸確認活躍 Client 存在時亦可完成。

驗證：Linux／Python 3.12.14／pySerial 3.5，完整 **182 項通過、無略過、無 RuntimeWarning**。新增 12 項單元與 7 項整合，覆蓋二進位、乾淨輪替與容量失敗、慢磁碟／佇列滿、部分寫入、fsync 錯誤、位移與 Epoch、損壞／缺 Footer／尾端部分行、無 Client、Replay 與 Client 關閉、兩路 RX／錄製故障隔離、API 健康事件、設定驗證及真 CLI／PTY SIGINT 收尾。另以無 Token 的真 CLI 檢查正常與損壞 Capture，核對中文輸出及 0／2 退出碼；檔案都在暫存目錄，未加入版本庫。

文件、CLI 繁體中文說明及 101 個相對連結／程式區塊檢查通過。此輪為 Implemented／自動化 Tested，Win11 單實體錄製、雙 UART 與 30 分鐘持續接收皆待使用者驗證；未操作公司 d3s1 或實體 COM3，無新增 UART TX、Confirm、Lease 或控制線操作。沿用既有 Git 交付方式保存至 `origin/work`，實際版本以提交紀錄為準。


## 錄製轉可讀文字（2026-11-06，Asia/Taipei）

使用者核准前次建議，完成 `uart recording-text <Channel目錄> --output uart-readable.txt`。沿用 recording_info 的有界掃描與完整性檢查，以同步回呼逐筆輸出全部分段，不需要 Token 或 UART。使用增量 UTF-8 解碼，跨正常分段接續；缺口、異常或 Epoch 變更切斷解碼狀態，不拼接出錯誤字元。時間、Epoch、原區塊位移與續字元標記保留，控制字元及無效 bytes 跳脫顯示；文字是檢視副本，不能替代原始 JSONL。

輸出採新檔獨占建立，既有檔案與原錄製不覆寫；不接受 .jsonl 輸出。部分可讀的異常 Capture 仍可匯出，但含明確警告及完整性摘要，退出 2；無已知缺口退出 0，操作／輸出失敗退出 1。輸出失敗可能留下部分檔案，沒有「匯出完成」摘要不得視為完成。已補上 Windows Quickstart，取代先前逐塊 PowerShell 解碼容易拆壞中文字、只處理首段的做法。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5 完整 190 項通過、無略過、無 RuntimeWarning。新增 8 項涵蓋跨分段 UTF-8、ANSI／控制字元、缺口與 Epoch、尾端部分行、原檔與既有輸出保護、回呼中的磁碟輸出失敗及無 Token 真 CLI 退出碼。原 182 項亦通過。未操作 Win11、實體 COM3 或公司環境；Windows 匯出、單實體錄製、雙 UART 與 30 分鐘接收繼續待驗證。依既有 Git 交付方式保存至 origin/work，版本以 Git 紀錄為準。


## Win11 文字匯出現場結果（2026-11-06，Asia/Taipei）

使用者於 `262f702` 交付與操作步驟後回報「可以看到文字。此測項 ok」。Win11 錄製轉可讀文字並查看此項記為 Verified（使用者執行並回報）。這是現場成功回報，不是 Agent 直接操作 Windows；未另回報檔案檢查摘要、退出碼、資料量或分段數，不將本次結果推論為零遺失、跨分段／缺口／磁碟失敗等全部情境亦已實機通過。

雙 UART 實機依使用者指示延後到公司，30 分鐘持續接收仍待測。本次僅更新文件，程式維持 `262f702`，沿用最近 190 項自動化通過證據，不重跑未變更程式；本次文件尚未提交／推送。


## 公司遠端連續 RX 30 分鐘結果（2026-11-06，Asia/Taipei）

使用者回到公司並完成前述遠端持續接收，回報「讀了30分鐘，且ipcam每秒會秀出ps資訊，所以會一直有資料。dxan container的資料正常」。Win11 Gateway 經 SSH Tunnel 至 d3s1／dxan 的 30 分鐘持續 RX 記為 Verified（使用者執行並回報）；來源每秒輸出，因此本次有持續資料，不是單純空閒等待。

未另取得退出碼、逐 byte／Offset 計數或錄製檔檢查摘要，不將本次成功推論為零遺失、錄製完整性或其他設備／速率的保證。公司遠端 30 分鐘項目已通過；家中本機長時間測試不另推論通過。下一個實機驗證為兩條 UART 同時接收及單路拔除後另一條繼續，仍需兩組設備。程式維持 262f702，僅更新文件，沿用 190 項自動化證據；文件尚未提交／推送。


## 離線錄製回看（2026-11-06，Asia/Taipei）

使用者要求先繼續開發可回家測的功能，雙 UART 實機測試延至晚上。提出離線回看為建議，提供選項供使用者調整後，先依此方向完成最小切片：`uart recording-play <Channel目錄> --terminal`。沿用錄製檢查器、UTF-8 增量解碼與既有 TerminalRenderer，依序快速回看所有分段，不按原時間等待、不需要 Token 或 UART。

預設安全文字跳脫，明確 --terminal 且 stdout 為 TTY 才呈現 CR／LF／BS／TAB 與允許的顏色；其餘控制序列仍過濾。正常分段延續解碼，缺口、Epoch／Binding 改變、裝置遺失或異常時重設；開始與最後摘要明示離線回看、尚未完成驗證／最終完整性。慢終端直接減慢掃描，不新增無界 Queue。Ctrl+C 通知工作執行緒停止，等待已開始 I/O 收斂、重設顏色並退出 130；未完成整份檔案檢查時不宣稱完成。正常無已知缺口退出 0，不完整／未知退出 2，操作／顯示錯誤退出 1。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5 完整 200 項通過、無略過、無 RuntimeWarning。新增 10 項測試涵蓋跨分段中文／顏色、CR／BS、控制序列過濾、非 UTF-8、缺口與世代／來源遺失、缺 Footer、顯示故障不重送、取消清理、無 Token 真 CLI 0／1／2 與 POSIX PTY 的終端模式。原 190 項亦通過。來源檔不改動，沒有新增 UART TX、API 或 Gateway 連線。Windows 視覺顯示及終端 Ctrl+C 尚待實測，未據此宣稱公司或居家硬體成功。

同步保存前次 Win11 文字匯出與公司遠端 30 分鐘實機成功回報；公司 30 分鐘不再列為待測，居家本機長時間與雙 UART 保留待測。操作見 Windows Quickstart。沿用使用者既有 Git 交付方式保存至 origin/work，提交版本以 Git 紀錄為準。


## 本機只讀互動式工作台（2026-11-06，Asia/Taipei）

使用者再次要求先開發可回家測的下一步。本輪先完成工作台設計，再新增 `uart menu` 與薄雙擊入口 `run-workbench.cmd`。使用同步文字選單，依既有 CLI 路由整合端點列出、單／雙通道選擇監看、錄製、套用既有 Profile 與錄製檔瀏覽／檢查／回看／文字匯出；無 GUI／TUI Framework，不新增協定或直接 Serial I/O。

錄製根目錄預設 recordings，可用 --recordings-dir 指定。錄製清單固定兩層掃描，上限 2000 個目錄項目，僅保留最近 50 個候選；超限時明示並提供手動 Channel 目錄。所有選擇、路徑以結構化 argv 路由，不經 Shell；Profile 與文字輸出繼續遵守原有新檔建立政策。沒有自動選取第一個 UART 或最新錄製。

監控／回看中 Ctrl+C 等待原流程清理後返回工作台，提示輸入時 Ctrl+C 離開；Q／Enter／EOF 可取消或離開。一般操作錯誤可返回，但 LauncherCleanupError 必須穿過子操作向外傳遞，整個工作台結束，不會再開 Gateway。工作台為本機／離線入口，遠端 RX 仍使用既有顯式憑證的 SDK／CLI。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5 完整 215 項通過、無略過、無 RuntimeWarning。新增 13 項單元涵蓋 TTY／EOF／取消、命令路由、只讀參數、一般與致命錯誤、Ctrl+C、路徑、掃描上限與控制字元；2 項真 CLI／PTY 整合完成選單檢查／回看／匯出，以及只 Mock 枚舉的真 Gateway／SDK／pySerial 錄製、SIGINT 清理、返回工作台、Capture 正常收尾與 Serial 重開。後者確認只有一個自有 Gateway 且已結束。

Windows CMD 雙擊、實體 UART 工作台流程、終端 Ctrl+C 與雙 UART 仍待使用者回家測，沒有將 Linux PTY 視為 Win11 證據。公司遠端 30 分鐘與 Win11 文字匯出的既有成功回報保留；本輪沒有實體 TX、Confirm、Lease 或控制線新操作。完成文件與格式檢查後，沿用既有 Git 交付方式保存至 origin/work，版本以 Git 紀錄為準。


## 遠端只讀工作台（2026-11-06，Asia/Taipei）

使用者在計畫階段選定「遠端工作台」、沿用 CLI 連線參數、第一版一條 Channel，並明確要求實作計畫。本輪完成 `uart --host ... --port ... --token-file ... menu --remote`，亦可用原 --token-env／預設 UART_GATEWAY_TOKEN。--remote 與本機 --recordings-dir 互斥，本機 menu 及雙擊入口保留。

遠端以 SDK 查詢 read 授權清單，顯示名稱、狀態、端點、錄製健康度與錯誤；明確選擇一條 ready Channel，再選 L／Enter 即時，或 R 最早仍保留的歷史＋即時。一次查詢／監看各使用及清理一條 SDK 連線，同步輸入在事件迴圈之外，沒有 input 背景執行緒。每次新連線重讀 Token 來源；失敗不自動重連，需按 R 更新或離開。

監看前與訂閱回覆均核對所選 Instance／Binding Revision／Epoch；不符則取消失效訂閱並要求重選，尚未建立畫面 Writer、不顯示 RX。沿用既有 watch_client／有界輸出／終端過濾器，不改 Server API。監看中 Ctrl+C 清理自己的 SDK 連線後返回快照清單；選單處 Ctrl+C 退出 130，Q／Enter／EOF 正常離開。工作台不建立／停止 Gateway，不執行錄製設定、Confirm、Lease 或 TX。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5 完整 234 項通過、無略過、無 RuntimeWarning；原 215 項亦通過。新增 12 項單元含模式、空清單／未就緒、AUTH_FAILED、Token 重讀／檔案錯誤／逾時、手動恢復、TTY／EOF／Ctrl+C、顯示字元及選擇／訂閱競態；7 項真 Gateway／SDK／Fake Serial／CLI／PTY 整合含 ACL、即時／重播、連線收斂、Instance／Epoch 競態、裝置遺失、網路中斷及 Token 檔更新後 R 恢復。實際 CLI Ctrl+C 返回後，Gateway 仍持續 RX，Fake Serial TX 次數為零。

Windows Quickstart 提供單一 PowerShell 環境啟動手動 Gateway 子視窗及遠端工作台、共用環境變數而不在 argv 放 Token 的居家步驟；並補上 dxan 使用既有 Token 檔的命令。此輪未連 d3s1 或實體 COM，Win11 同機／公司新選單仍待驗證，既有公司 30 分鐘成功不視為新入口已通過。依核准計畫檢查文件後提交並推送 origin/work，版本以 Git 紀錄為準。


## 居家第 1～4 項通過與遠端 USB 插拔恢復（2026-11-06，Asia/Taipei）

使用者明確回報先前清單第 1、2、3、4 項成功，第 6 項雙 UART 尚未測。記錄更新／枚舉、本機工作台監看錄製及清理返回、離線檢查／回看／匯出，以及 Win11 同機遠端工作台一般流程為使用者驗證通過；未取得所有負向子情境的個別資料，不擴大為全部異常／容量或零遺失均已驗證。

第 5 項使用者實際操作 menu --remote，拔除時觀察斷線與退出，插回後重新執行仍無法監看。提供的重開後 Log 顯示同一 Gateway Instance 可以認證及查詢，但 ch_uart_1 為 lost／DEVICE_MISSING；R 查詢後狀態仍相同；本機枚舉已重新看到 COM3。因此這不是從此 Log 可判定的認證失敗，Gateway 端也不會因枚舉重新出現就自動重開 Serial。原第 5 項指本機 Launcher 清理再選擇流程，與遠端既有 Gateway 的恢復方式不同。

Log 最後的「已離開遠端工作台」符合原本 Q／空白 Enter 的離開分支，未顯示 Traceback 或 EOF 提示；沒有拔除瞬間完整輸出，不宣稱已確認首次退出的來源。為避免恢復時空白輸入讓選單離開，本輪新增恢復模式：認證／連線／訂閱失敗，或所有通道未就緒時 Enter 保持等待，不退出、不新增 SDK 連線；R 才查詢，Q／Ctrl+C 可離開，EOF 明示結束。重新查到 ready 後，正常清單 Enter 仍可離開。

lost／faulted 清單新增中文提示：R 只查詢、不重開 Serial；需在 Gateway 電腦核對 COM，正常停止原程序，以原設定與相同 Token 手動重啟，再 R 並重新選擇。重啟會影響該 Gateway 全部通道及記憶體歷史，介面只提示、不代操；沒有新增 API、重試、Confirm、Lease、TX 或控制線操作。

Linux／Python 3.12.14／pySerial 3.5 完整 239 項通過、無略過、無 RuntimeWarning；新增 4 項恢復選單測試及 1 項真 CLI／PTY／Gateway 整合。後者重現 lost 後空白仍等待、端點重新可開啟但 R 不自動重開，並由測試端手動以同 Token 重啟真 Gateway；UI R 取得新 Instance 後再次收到 RX。此為 Fake Serial 模擬，不是 Windows USB 實體復原證據。原本機 USB 插回再接收、遠端手動重啟恢復與此新版恢復提示仍待使用者重測；第 6 項繼續待測。依既有 Git 交付方式保存至 origin/work，版本以提交紀錄為準。

## Gateway 狀態日誌與單通道人工 RX 重開（2026-11-06，Asia/Taipei）

使用者在回報 Gateway 只顯示接聽訊息、USB 插回仍 lost 後，明確要求「先補日誌，再做單通道手動 RX 重開」。本輪先補 Server 狀態觀測：不依賴 Recorder 或 Client，Gateway 命令入口將開啟、就緒、遺失、故障、關閉與人工重開請求／失敗印到 stderr，含通道、Instance、端點、修訂、Epoch 與錯誤碼；不含 RX 原文或 Token。不新增持久日誌檔或完整 Audit 儲存。

先更新協定再實作 V1.0 可選能力 `channel.reopen_rx`；沿用原 configure 權限與 Expected Context，不修改既有方法／快照／事件。Core 僅接受 lost／faulted／closed，重用原 Rebind 的失效與收斂程序，再經 SerialPort 的 RX 恢復介面開啟；對 ready、生命週期進行中、尚有 Driver／Worker、Lease 或寫入未完成時拒絕。接受後 Revision 增加，成功新 Epoch／Offset 0／unconfirmed；失敗不回退修訂，不因晚到完成建立有效世代。保留 TX blocked，不 Confirm、不取得 Lease、不傳送 bytes、不增加額外控制線操作。只處理指定 Channel，其他通道／訂閱、Gateway Instance 與已寫入錄製檔保留。

實體 Backend 重新枚舉、套用全部原 Selector，RX 重開時另核對前次成功開啟的已知 VID／PID／序號；無序號時核對已知位置。不符或已知資訊消失拒絕，始終無身分資訊時仍為 unverified，由人工核對接線，不保證辨識同 Adapter 換板。此額外限制只用於 RX 恢復，一般 Rebind／Confirm 開啟維持既有 Selector 語意。

CLI 新增 `uart reopen <Channel>`；遠端工作台先查到 capability 與 configure 才提供不可用通道的 O／Y 動作，另建 SDK 連線核對所選快照；成功更新清單並讓使用者再選監看。R 或 USB 插回不重開；失敗、斷線或本地逾時不自動重送。原 COM3 範例與 Launcher 臨時 read 權限不變，另提供 `examples/gateway.com3.rx-reopen.toml` 明確選用 read＋configure、無 tx。configure 同時包含既有 Binding 管理，不宣稱是專用細分權限。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5，`.venv/bin/python -W error::RuntimeWarning -m unittest discover -s tests -v` 完整 **262 項通過**，21.380 秒、無略過、無 RuntimeWarning。新增 23 項，原 239 項亦通過；證據涵蓋無 Client／Recorder 日誌且不洩漏 Token／RX、嚴格協定參數與範例、權限及舊快照／重複操作拒絕、開關失敗／占用／裝置缺少、身分核對、Worker／Driver 逾時與晚到完成、能力缺少、結果不明不重試、選單取消與確認、真 CLI／PTY 同 Instance 恢復、其他通道 RX／訂閱持續、錄製新舊 Epoch 與既有 unknown 完整性，以及真 pySerial／PTY 關閉後重新開啟接收。新恢復路徑未產生 UART 寫入；既有 TX 模擬測試仍屬原範圍。

另檢查新舊 COM3 配置可由既有載入器讀取，原檔仍 read、管理檔 read＋configure，均未因此開啟 Serial。Windows Quickstart、工作台、設定、架構與路線圖已同步。此輪沒有存取 Win11 實體 COM3、MobaXterm、d3s1 或 dxan；新版 USB 拔插／重開／占用、管理權限及雙實體隔離須由使用者實測。既有使用者第 1～4 項與公司 30 分鐘通過證據保留，第 6 項雙 UART 繼續待測。依既有 Git 交付方式提交並推送 origin/work，版本以 Git 紀錄為準。

## Win11 單通道 RX 恢復驗收（2026-11-07，Asia/Taipei）

使用者依 6bb40bf 後提供的 Win11 同機 COM3 測試步驟回報「測試都通過」。記錄正常接收、USB 拔除時 Gateway 狀態日誌及 Client 返回恢復選單、插回後 R 僅查詢、O／Y 手動重開後重新接收，以及未插回時 DEVICE_MISSING、MobaXterm 占用時 PORT_BUSY、釋放占用後人工恢復為 Verified（使用者執行並回報）。流程預期維持同 Instance、新 Epoch／未 Confirm；未另取得原始 Log 或逐 byte 計數，不據此聲稱零遺失、控制線量測或全部權限／競態實機通過。

本次是使用者現場回報，不是 Agent 直接操作 Win11。雙 UART 實體隔離、公司 dxan 經 SSH Tunnel 的新版重開操作仍待測；原公司 30 分鐘成功證據保留。僅更新驗收文件，程式維持 6bb40bf，沿用最近 262 項自動化通過證據，不重跑未變更程式。

## Win11 雙 UART 故障與恢復隔離驗收（2026-11-07，Asia/Taipei）

使用者確認 COM3 與 COM4 各接一台持續輸出的 IPCAM，依本次提供的雙通道測試步驟回報「測試正常。com4 未曾中斷。」。測試使用手動啟動的同一 Gateway 與私人 dual-rx-test.local.toml，COM4 以獨立 Client 持續監看，COM3 經遠端工作台拔除、插回、R 查詢、O／Y 手動重開並再次監看。此方向的雙實體故障／恢復隔離記為 Verified（使用者執行並回報）。

步驟包含前後 Epoch 比較；本次未附快照、原始 Log 或逐 byte 計數，不據此聲稱零遺失、反向拔除 COM4、雙路錄製完整性、雙擊 Launcher／Profile 全流程或五至六條容量均已通過。此為 Win11 同機結果，不是公司 dxan 經 SSH Tunnel 的新版管理操作驗證；該項仍待測。

本次僅更新驗收文件並保留前次單通道成功紀錄，程式仍為 6bb40bf，沿用既有 262 項自動化通過證據，未重跑未變更程式；已檢查文件格式與相對連結。私人配置、Token 與 UART 原文未加入版本庫；使用者已授權提交並推送本輪驗收文件。

## 公司驗證排程與交接（2026-11-07，Asia/Taipei）

使用者授權 commit／push 本輪單通道與雙 UART 實機驗收文件；公司 dxan 經 SSH Tunnel 的新版單通道手動重開驗證，延至下週回公司後再測，維持待驗證，不阻擋居家開發。程式維持 6bb40bf，沿用最近 262 項自動化證據。本次只有文件變更，未執行公司測試或新增產品功能。

## 遠端雙路同時監看工作區（2026-11-07，Asia/Taipei）

使用者核准開始實作前次提出的雙通道遠端工作台。在 menu --remote 清單輸入兩個不同編號（例如 1,2），可同時監看最多兩條，選擇即時或各自歷史＋即時；未就緒的一路等待，其他路正常接收。每批／每行顯示 Channel／名稱，重用既有 UTF-8、終端過濾、來源標記與有界 Writer，顏色獨立，CR／退格跳脫，無分割窗格／TUI Framework。

新增 remote_group 協調工作區，透過注入的 SDK 連線、watch_client 與共用 Writer 操作；每路獨立 Session／事件佇列，沿用 Instance／Revision／Epoch 及訂閱回覆核對。原 Gateway、Core、SDK、API Contract、四路／八 Client 容量均不改。單路裝置遺失、網路失聯或快照失效不取消另一路，也不自動重連；共享顯示故障／佇列滿才結束整組並清理。跨通道的輸出先後不保證，原 bytes 不因畫面轉換改變。

畫面內 R 只查詢與更新權限／狀態，O 1／O 2 選一路再 Y 確認，使用既有 configure／能力與 Expected Context 重開；等待確認或慢 Driver 開啟時另一路持續。成功只替該路建立新世代的歷史＋即時監看，維持未 Confirm，不授予 Lease／TX，不改控制線。取消／失敗／結果未確認不自動重試；S 1／S 2 可對已停止、快照 ready 的一路人工重新訂閱即時資料。R 發現新 Instance 後，需 Q 返回外層並重選，不在舊工作區操作新 Gateway。Q／Ctrl+C 清理雙路 Client 後返回；外層 R 才查詢，Q 離開。重開等待被中止亦明示結果可能未確認。

console_input 使用 Windows msvcrt／POSIX select 非阻塞輪詢，每輪最多 128 字元、指令緩衝最多 128 字元；不建立 input 工作執行緒，不解析 UART 成為操作。Windows 指令輸入也經同一 Writer 顯示；RX 會穿插提示，屬合併文字畫面。新增 examples/gateway.dual.rx-reopen.toml，明確指定 COM3／COM4 與 read＋configure，無 tx；載入檢查通過且沒有開啟 Serial。私人配置與原 Token 來源可以沿用，不須為新 Client 重啟既有 Gateway。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5，以 .venv/bin/python -W error::RuntimeWarning -m unittest discover -s tests -v 執行完整 **283 項通過**，23.615 秒、無略過、無 RuntimeWarning。新增 21 項，原 262 項亦通過。涵蓋模擬 Windows 鍵盤編輯／特殊鍵／Ctrl+C／EOF／容量、POSIX 非阻塞逐行讀取、雙路選擇及取消、來源／顏色、重播、重複訂閱拒絕、單路快照失效、裝置／網路／認證錯誤隔離、管理確認／取消／舊快照／權限／占用、慢開啟期間另一路接收且訂閱／Epoch 不變、共享輸出故障／容量不足，以及 EOF／Q／SIGINT／重開等待中止的清理與不明結果提示。真 CLI／PTY 完成 1,2 選擇、拔除、等待 Y 時另一路持續、重開後接收、返回清單且 Gateway 繼續；新路徑 Fake Serial UART 寫入次數為零。

此輪未存取 Win11 實體 COM3／COM4 或公司環境。前次雙 Client 實機隔離與單路恢復成功保留；本次合併畫面、Windows 鍵盤／Ctrl+C 與新工作區實體重開仍待使用者測試，公司 dxan 新操作維持下週回公司後再測。雙路錄製完整性、完整 TUI、五至六條容量與其餘 Phase 1 驗收不據此完成。同步更新 README、工作台、Windows Quickstart、架構、設定、開發規範與路線圖；沿用 work 分支交付，提交版本以 Git 紀錄為準。

## 雙路長歷史重播斷線修正（2026-11-07，Asia/Taipei）

使用者回報雙路工作區 Q 返回後，重新選 1,2 的 L 即時正常，但 R 歷史＋即時只有一路接收；提供 Log 顯示 ch_uart_2 在建立重播時出現「Gateway 已關閉連線」，ch_uart_1 較短歷史仍正常。這不等於 USB 遺失或 S 指令使用錯誤。Agent 以真 Gateway／CLI／PTY、第一路 20 與第二路 1200 個小歷史區塊重現：原 Server 在 rx.subscribe 後同步將全部 Replay 塞入訂閱輸出佇列，較長歷史的序列化訊框超過 262144 bytes 上限而 abort 該 Session。原版回歸測試未通過，先前的小歷史測試未跨過此界限。

Server 改由 Session 單一 Writer 逐筆編碼／傳送歷史快照，Response 優先、不同訂閱輪流、同訂閱 Gap／Replay／Live 有序。歷史快照獨立計費與有界保留：每 Session 共用兩倍 RX Ring 配額，payload＋每事件 512 bytes 的 Metadata／引用預留，期限沿用 idle_timeout_ms，預設 15 秒。配額不足以既有 QUEUE_FULL 拒絕並撤銷該次新訂閱，不關閉既有訂閱；取消、Epoch 結束或關閉立即清空剩餘快照引用。原即時與 Connection 輸出限制不變，真正慢接收超限或重播超時仍關閉該連線並印出原因；沒有忽略資料、擴大原即時佇列、加固定 Sleep 或自動重新訂閱。Core、SDK、API 方法／欄位／錯誤碼不變，不增加 UART TX。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5，.venv/bin/python -W error::RuntimeWarning -m unittest discover -s tests -v 完整 **292 項通過**，22.748 秒、無略過、無 RuntimeWarning；新增 9 項，原 283 項亦通過。真 CLI／PTY 的 Q→R 查詢→1,2→R 已確認兩路完整測試歷史尾端皆出現，隨後兩路都有新即時資料；跨通道到達順序不作固定假設。新增 Session 單元覆蓋超過原訂閱佇列的歷史逐筆傳送、Response／Gap／Replay／Live 順序、輪流排程、快照配額、Live 仍有界、期限釋放與傳送中取消；真 SDK 覆蓋快照拒絕後不留新訂閱且原連線仍可讀取。

這是自動化模擬與 POSIX 證據，未直接存取 Win11 COM3／COM4 或公司環境，不宣稱使用者的 Windows 情境已重測成功。新版需正常關閉並重啟 Gateway 才載入 Server 修正，保留原配置與 Token；只重開 Client 不足。重啟的記憶體歷史會清空，Quickstart 提供累積持續輸出後再 Q／R 重播的驗證方式；錄製檔仍保留。Win11 長歷史雙路重測與公司 dxan 新操作仍待回報，公司維持下週回公司後再測。文件已同步，依既有 work 分支交付方式提交／推送，版本以 Git 紀錄為準。

## Win11 雙路重播修正後重測（2026-11-07，Asia/Taipei）

使用者在 03fbc96 交付及更新 Gateway／雙路 R 重播驗證步驟後回報「目前測試正常」。雙路歷史＋即時的一般重測記為 Verified（使用者執行並回報），不再將此案例列為僅有模擬證據。未另取得原始 Log、累積時間、歷史資料量或逐 byte 計數，不推論已完成建議的 15 分鐘、所有容量或零遺失驗證。先前新畫面 Q／O／Y 的實際操作證據保留，公司 dxan 新操作仍按使用者安排下週回公司後再測。

下一個建議驗收是雙路錄製完整性：正常結束時分別檢查兩路；另錄一輪，拔除一路後確認另一路持續，停止後遺失一路應為連續性未知、另一路正常收尾。此項尚未執行，不新增功能或標為成功。本次僅更新文件，程式維持 03fbc96，沿用既有 292 項自動化通過證據，未重跑未變更程式；文件尚未 commit／push。

## Win11 雙路錄製單路遺失驗收（2026-11-07，Asia/Taipei）

使用者依雙路錄製步驟執行 local --select --count 2 --read-only --watch --terminal --record-dir .\recordings，中途拔除 COM3。提供的 Log 顯示 ch_uart_1 因 DEVICE_MISSING 結束訂閱，ch_uart_2 仍有後續資料；停止後以 recording-info 分別檢查本次 Capture，摘要如下。

| 通道／端點 | 分段 | RX 區塊 | RX bytes | 正常收尾 | 完整性檢查 |
| --- | --- | --- | --- | --- | --- |
| ch_uart_1／COM3 | 1 | 11 | 374 | 是 | 連續性未知；來源裝置曾失去連線或故障 |
| ch_uart_2／COM4 | 1 | 172 | 5168 | 是 | 對已觀測資料無已知缺口 |

這輪雙實體錄製故障隔離記為 Verified（使用者執行並提供 Log／摘要）：來源遺失未被誤報完整，另一路仍持續且正常收尾。正常收尾與資料連續性為不同檢查；COM3 的 Footer 正常不會消除裝置遺失造成的未知。COM4 命令亦已完成並返回 PowerShell，不是卡住。

未取得原始錄製檔，不將區塊數等同固定秒數，不宣稱 UART／USB／Driver 前段零遺失、雙路皆健康的一輪、反向拔除、分段輪替、長時間或磁碟故障均已實測。此項不包含錄製中手動重開的新舊 Epoch 驗證。公司 dxan 新操作仍按使用者安排下週回公司後再測。

本輪僅更新驗收文件，保留前次雙路重播重測成功；程式仍為 03fbc96，沿用既有 292 項自動化通過證據，未重跑未變更程式。沒有保存私人配置、Token、UART 原文或錄製檔；文件尚未 commit／push。

## Win11 錄製中單路手動重開驗收（2026-11-07，Asia/Taipei）

使用者以具 configure 權限、啟用 recording 的手動雙通道 Gateway，完成 COM3 拔除／插回／O 1＋Y 重開並繼續錄製。前後 channels 快照顯示 ch_uart_1 ready／EpochSame=False／unconfirmed，ch_uart_2 ready／EpochSame=True／unconfirmed。正常停止後，兩路 recording-info 的正常收尾均為是，各一分段；COM3 為連續性未知，COM4 對已觀測資料無已知缺口。

使用者補充以 ASCII 鍵值與解碼 payload 計數的逐 Epoch 統計：

| 通道 | Epoch | 首筆 offset | RX 區塊 | RX bytes |
| --- | --- | --- | --- | --- |
| ch_uart_1／COM3（重開前） | ee2ddb2df17148da933962dd9d14bcbc | 0 | 91 | 3094 |
| ch_uart_1／COM3（重開後） | 98f9cc5a7e5c4ddb9fa3d57303fd2a52 | 0 | 50 | 1700 |
| ch_uart_2／COM4 | e1529020e7ce48cbae38a8b1e92f0b3c | 0 | 171 | 5304 |

COM3 合計 141 區塊／4794 bytes，COM4 合計 171 區塊／5304 bytes，均與同次 recording-info 一致。中斷前後資料保留並以不同 Epoch 區分，COM4 維持原世代，重開不會把 COM3 曾遺失的連續性改報完整。本次錄製中單路 RX 手動恢復記為 Verified（使用者執行並提供快照／檢查／統計），不是 Agent 直接操作 Windows；未據此宣稱前段零遺失、反向拔除、長時間／輪替或磁碟故障也通過。

第一版臨時核對腳本含中文資料鍵值，經 PowerShell 管線後變成問號；區塊數及資料量都變成同一個 ???，使數值累加成 bytes＋chunks。修正版只用 ASCII 鍵值，重新讀取同一錄製檔後統計吻合，無需重做實驗；此為臨時核對腳本的編碼問題，沒有證據指出產品錄製格式損壞。

本輪只更新驗收文件與操作核對說明，程式仍為 03fbc96，沿用最近 292 項自動化通過證據，未重跑未變更程式。保留前次實機成功紀錄，公司 dxan 新操作維持下週回公司後再測；私人配置、Token、UART 原文與錄製檔未加入版本庫。文件尚未 commit／push。

## recording-info 原生 Epoch 摘要（2026-11-07，Asia/Taipei）

使用者核准先保存實機驗收文件，再開發 recording-info 內建逐 Epoch 統計。前次 Win11 雙路重播、單路遺失錄製及錄製中手動重開的快照／數字已保存為文件提交 61b4bea；這些是使用者現場回報，不代表新摘要命令已實機驗證。

新增 inspect_capture_summary，使用原 inspect_capture 的同步 on_record 回呼，在同一次逐行掃描中聚合；原函式、匯出／回看回呼及回傳值保留。CLI 原命令與工作台檢查直接顯示通道／Capture／Instance、每 Epoch 首筆 offset／末筆 next_offset（不含）、區塊與解碼後 bytes，按檔案順序列出建立／切換、遺失、故障、開啟／關閉及已知缺口。只見 Epoch 改變不推論具體重開原因；有 ready Epoch 但無 RX 時列零筆及無 RX。

最多保留 1024 個 Epoch、256 筆邊界事件及 128 字元摘要識別；超限後仍完成原檔案掃描，明示未完整列出並計算未列區塊／bytes。已列與未列共同核對原 RX 總計，不以位移跨度代替 payload 長度。摘要本身不是每世代完整性認證；原雜湊／序號／位移／Footer／未知檢查與 0／1／2 退出碼保留，核對不一致則標 incomplete。沒有新增 Token、API、UART I/O、檔案格式或來源改寫；先前指示使用的 recordings-reopen-test 目錄已加入忽略清單。

Implemented／自動化 Tested：Linux／Python 3.12.14／pySerial 3.5，以 .venv/bin/python -W error::RuntimeWarning -m unittest discover -s tests -v 執行完整 **304 項通過**，23.462 秒、無略過、無 RuntimeWarning。新增 12 項單元／真 CLI 測試，原 292 項亦通過；既有真 Gateway 錄製／重開整合案例另擴充新摘要及離線 CLI 驗證。涵蓋 91＋50 區塊／3094＋1700 bytes 的合成案例、跨分段／二進位、缺口、缺 Footer／壞雜湊、零資料、數量／長識別上限、輸出控制字元、總計核對、來源不改與無 Token 的原退出碼。原文字匯出／回看及單雙路工作台也通過回歸。

Windows Quickstart 已提供既有 recordings-reopen-test 的直接檢查步驟，不需重錄或重啟 Gateway；新顯示的 Win11 重測待回報，未直接讀取使用者實體錄製檔或存取 COM3／COM4。公司 dxan 新管理操作仍待下週回公司後再測，長時間／輪替／磁碟等實機驗收不擴大宣稱。本次功能、測試及文件沿用 work 分支提交／推送，版本以 Git 紀錄為準。
