# 設定規格

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

## 狀態、名稱與讀法

本文件是設定參數的唯一權威規格，已接受為初始基線。目前已有 Fake Serial 與實體 Serial TOML 範例及局部載入器，尚未實作表中所有設定。表中的點號表示設定路徑，`channels[]` 表示 Channel（邏輯通道）清單；不是線上 JSON 欄位的另一套定義。

## 目前實際支援範圍

啟動必須明確選擇 `--fake` 或 `--serial`，只枚舉使用 `--list-ports`，不開啟 COM。共通欄位為 `network.bind_host/port`、`gateway.lifecycle_timeout_ms`、`channels[].id/label/target_note/auto_open_rx/auto_reconnect`、Principal 與下述錄製設定；`auto_reconnect` 目前僅接受 false。實體模式另接受完整 `channels[].adapter` 與 `channels[].serial` 欄位；模擬模式不接受這兩組設定，避免誤認已開啟真裝置。其餘未支援欄位明確拒絕，容量與期限採基線預設。

Serial Backend 在 `port=None` 時先套用設定與 DTR／RTS 意圖，再開啟端點；不用 flush 等待或額外傳送 bytes。每次開啟重新以全部 Selector 條件匹配，模糊或不符時拒絕；只有明確 Port 且沒有其他條件時，允許枚舉缺少 Metadata 的端點。COM 名稱不是永久身分。Windows 使用 pySerial 的原生獨占開啟，POSIX 使用 exclusive，程序內另以端點預留防止不同 Channel 重複開啟。

開啟失敗保留 Channel 錯誤與 TCP 管理介面，其他通道繼續；read 空逾時不算拔除，read 錯誤使租約與舊訂閱失效。生命週期等待逾時後維持 faulted、不讓晚到結果確認目標，未收斂 Handle 不得交接。這是邏輯等待上限，不能安全強殺卡死的 Driver 執行緒；極端狀況仍需人工處理程序。

`token_secret_ref` 現支援 `env:變數名稱` 或 `file:檔案路徑`，不接受文件內的真實 Token。`file:` 必須指向受保護、以 UTF-8 儲存 Token 的檔案；權限由部署者管理。CLI／SDK 預設使用下節固定連線；`--token-env` 或 `--token-file` 保留明確來源相容。完全沒有固定設定時才沿用原 `UART_GATEWAY_TOKEN`，沒有直接傳入 Token 值的命令列選項。

「確認」只表示使用者已明示；「建議」不代表已核准。`必填` 不提供猜測值；`可省略` 採表中候選預設；`條件必要` 依說明判斷。所有容量均有上限，不允許 `0` 表示無限。秘密只透過參照載入，不在文件或版本庫放真值。

所有設定在 Phase 1 候選方案中於啟動載入；不提供任意熱更新。Binding（綁定）確認是經授權的執行期操作。變更 Serial 設定需停止舊操作並建立新 Epoch（資料流世代），不是直接覆寫欄位。

2026-11-06 新增單通道人工 RX 重開，不讀取或修改 TOML。`uart reopen <Channel>` 或遠端工作台 O／Y 使用原設定，需 configure 權限及 Gateway 擴充能力；ready 通道拒絕操作。原 `examples/gateway.com3.toml` 及所有本機 Launcher 臨時配置維持 read；明確選用 `examples/gateway.com3.rx-reopen.toml` 才給 read＋configure，仍沒有 tx。configure 也允許既有 Binding API，並非專用細分的 reopen 權限，請僅授予通道管理者。

實體 Backend 的 RX 重開另外核對本程序前次成功開啟的已知 VID／PID／序號；無序號時核對已知位置。不符或已知資訊消失即拒絕，不因同 COM 猜測是原 Adapter。一直沒有 Metadata 的明確 Port 仍為 unverified，需人工核對接線。一般 Rebind／Confirm 的既有 Selector 語意不變；重開不改 UART、DTR／RTS，不授予確認或 TX 資格。完整契約見 [協定](protocol/v1.md)。

Gateway 命令入口預設以 INFO 等級將時間與 Channel 狀態日誌寫到 stderr，lost／faulted 用 WARNING，無 Client、未錄製時同樣輸出。日誌不含 RX 原文、Token 或秘密內容，不新增持久檔案與日誌保留設定；這不是尚未完成的完整 Audit 儲存。

## SDK 遠端自動認證

本機 `local`／`start` 沿用原本的臨時或明確設定來源。本節是遠端固定連線：Server 的 auth.authenticate、權限欄位、Confirm／Lease／TX 及線上版本不變。完整操作見 [認證初始化](authentication.md)。

| 來源／參數 | 現行行為與上限 |
| --- | --- |
| Windows 預設根目錄 | `%LOCALAPPDATA%/uart-gateway`；缺 LOCALAPPDATA 時使用目前使用者 AppData/Local |
| Linux 預設根目錄 | `$XDG_CONFIG_HOME/uart-gateway`，未指定則 `~/.config/uart-gateway` |
| CLI `--connections-dir`／SDK `directory` | 明確的根目錄覆寫；初始化與匯出不將秘密寫入本專案版本庫 |
| `default-connection` | 最多 128 bytes 的預設連線名稱檔；首次成功安裝時設定，auth use 可明確更換 |
| `connections/<名稱>/client.json` | 最多 16 KiB；名稱 1～64 個 ASCII 英數字／底線／連字號 |
| 設定 JSON | version 整數 1、deployment_id 為 32 個小寫 hex、host、port、credentials；可含 gateway_config 路徑；拒絕未知欄位 |
| credentials | 僅 read／manage 的 file: 或 env: 參照；不得保存 Token 值；file: 相對路徑以該連線目錄解析，不以目前目錄解析 |
| 自動來源端點 | host 僅 127.0.0.1／::1，port 為 1～65535；經既有 SSH Tunnel 使用遠端 Gateway，不新增 TLS 或外部 Listener |
| `--connection` | 明確選定名稱，省略使用 default-connection 或名稱 default |
| `--access` | read／manage；固定連線省略時為 read，管理須明確選擇 |
| `--host`／`--port` | 只有明確輸入才覆寫固定端點，即使值等於原 CLI 預設也算覆寫 |
| 秘密檔 | 最多 1024 bytes、UTF-8、去頭尾空白；自動來源須符合既有 43～128 ASCII Base64url 格式 |
| 權限保護 | POSIX 擁有者須為目前使用者且無 group／other 權限，建立檔案 600／目錄 700；Windows 寫入前配置及讀取時檢查目前使用者 ACL |
| SDK | Client.connect_auto／resolve_connection／ConnectionSettings.connect，共用來源讀取與角色核對 |

讀取連線只接受 Server 實際授權 read；管理只接受 read＋configure 的範圍，且至少一個通道有 configure。角色與授權不符時關閉連線，不返回 Client，不改用其他秘密；Server 仍是最終授權者。固定角色不接受 tx，原 `Client.connect(host, port, token)` 與明確來源的既有 TX 工作流程保持相容，仍受 Confirm／Lease 等保護。

來源優先序：明確 --token-file／--token-env 使用唯一指定來源，與 --connection／--access 不得混用；否則已配置的固定來源優先於任何殘留 UART_GATEWAY_TOKEN。只有完全沒有固定設定且未明確指定連線／角色時，才相容舊 UART_GATEWAY_TOKEN。已存在但損壞、缺檔或認證失敗不觸發降級，也不自動生成另一把遠端 Token。SDK 每次新連線重讀所選秘密；工作區建立後固定端點及來源參照，更換預設設定需重新進入工作區。

### 一次性初始化與傳輸

| 命令 | 行為 |
| --- | --- |
| `auth init --gateway-config 檔案 [--name 名稱]` | 來源 UTF-8／BOM TOML 最多 64 KiB；共用原驗證，建立新 read／manage 秘密、兩身分 Gateway 副本與 client.json；原檔不變，不開 Serial／Listener |
| `auth export [--name 名稱] [--access read或manage] [--output 路徑]` | 預設 read，產生新的受保護 .uart-credential 包；預設放私有 transfers 目錄；不輸出 Token，不覆蓋既有檔 |
| `auth import (--file 檔案或--stdin) [--name 名稱] [--port 本機Port]` | 包最多 16 KiB；檔案需私有權限，stdin 僅管線。部署與 Port 相同可增加另一角色；相異既有秘密拒絕覆寫，相同內容可重複匯入 |
| `auth use 名稱` | 更換預設連線，仍不提升預設讀取權限 |

包是一次性秘密傳輸資料，不是普通設定檔；欄位僅 version=1、deployment_id、access、token、port，驗證格式後才寫入。部署 ID 只用於拒絕混合不同部署，不是 Server 身分驗證或 wire 欄位。傳輸沿用 SSH，無匿名配對端點；Server 啟動時載入 file: 秘密，輪替需協調更新與重啟，本輪不提供自動輪替／reload。

SDK 模組匯入不讀檔或連線；檔案 I/O 在工作執行緒，Token 不保存於連線設定物件 repr、命令列、一般設定或 Log。CLI read_client_token 僅保留相容薄轉接，來源處理由 SDK 提供。Windows ACL 的實際環境相容性仍待實機驗證；Linux SDK 不需要 Windows PowerShell。

## Gateway 與網路

歷史重播由 Session Writer 逐筆送出，使用獨立有界快照，不占用整段即時輸出佇列。每 Session 快照共用上限為目前 RX Ring 設定的兩倍（預設 8 MiB，原始 payload＋每事件 512 bytes 預留），期限沿用 idle_timeout_ms（預設 15 秒）；不新增 TOML 參數或修改 Hello 欄位。Writer 額外至多持有一個不超過 max_frame_bytes 的編碼中／傳送中 Frame。即時訂閱佇列及 Connection 輸出上限維持原值，快照配額不足回 QUEUE_FULL，慢接收超限或重播期限到達仍會明示斷線／記錄原因。

遠端雙路工作區在 `menu --remote` 清單輸入兩個不同編號啟用，最多兩路，不新增設定欄位。雙路共用原顯示 Writer（256 項／262144 bytes，含寫入中批次），每路各用獨立 SDK 有界事件佇列；另建短期管理連線。`examples/gateway.dual.rx-reopen.toml` 是 COM3／COM4 明確選用的 read＋configure 範例，沒有 tx，不影響既有只讀配置。工作區指令上限 128 字元，R／O／S 皆需人工觸發；原 API 與 Gateway 四路／八 Client 上限不變。

### 本機啟動器配置（已實作）

互動工作台 `uart menu --recordings-dir 路徑` 的根目錄預設為目前工作目錄下的 `recordings`，用於本次錄製及離線瀏覽，不寫入下列端點 Profile 或 Server 的持久設定。`uart --port 18082 menu` 可指定本機 Gateway 的 TCP Port；工作台為本機／離線入口，不接受外部 Token 或遠端 host 選項。操作與掃描上限見 [工作台說明](workbench.md)。

遠端工作台以 `menu --remote` 明確啟用，與 --recordings-dir 互斥。連線參數由 SDK 固定設定載入，可用全域 --connection／--access 選擇；明確 --host／--port 可覆寫端點，--token-file 或 --token-env 則為互斥的舊來源模式；Client Port 可為 1～65535（例如 SSH 本地轉送的端點），不套用 Server 綁定 Port 的限制。每次連線重讀所選憑證；固定設定保存於使用者私有目錄，不寫入本機工作台偏好；只列出有 read 權限的 Channel。有權限與擴充能力時提供單路手動 RX 重開，不提供 TX、遠端設定修改或完整 reload。

`uart local --select --count 2 --save-profile bench.local.toml` 保存的是本機選擇配置，與 `uart-gatewayd --config` 的伺服器配置分開。前者沒有 network、auth、UART 修改或控制線選項；啟動器驗證後才轉為既有伺服器 TOML，Serial 固定 115200／8N1／無流控、DTR／RTS false。直接交給伺服器的完整設定格式不變。

| 欄位 | 格式與限制 |
| --- | --- |
| `version` | 整數 1 |
| `channels` | 1～2 個通道；不是將 Server／Gateway 的四條上限改為兩條 |
| `channels[].id` | 1～64 個 ASCII 英數字、底線或連字號；不重複。選單產生 `ch_uart_1`、`ch_uart_2` |
| `channels[].label` | 1～128 UTF-8 bytes、非全空白、可顯示且不重複的名稱；不是板子識別 |
| `channels[].adapter` | 必含 `port`，其他可含 `vid`／`pid`（成對）、`serial_number`、`location`，限制沿用下表；拒絕未知欄位 |

檔案上限 64 KiB，可讀 UTF-8／UTF-8 BOM；不允許 Token、權限、Confirm 或 Session 等額外欄位。保存時只建立新檔，不覆蓋既有配置；若後續監控失敗回到選單，重新選擇不會覆蓋首次保存的檔案。建議使用已被 Git 忽略的 `*.local.toml`，保存位置由使用者明確指定。

`--profile` 每次重新枚舉：有序號時暫不以舊 COM 篩選，以序號及其餘已存條件唯一定位後採用目前 COM；位置、VID／PID 條件仍必須符合。無序號時必須符合原 COM 與其他已存條件，不自動跟隨重編 COM。不存在、模糊或兩條解析到同一端點時，要求重新選擇。若只有 Port 可用，不能證明同名 Port 仍是原轉接器；即使有序號也不能識別轉接器換接另一塊板子。配置不因解析到新 COM 而被自動改寫。

全部端點鎖與 Ready 核對完成才開始監控；任一啟動失敗撤銷本次整組。啟動後單條失去連線只停止其訂閱，其餘繼續；沒有自動恢復該條。`--select` 在全組因錯誤停止且清理完成後返回選單；`--profile` 則退出。到達正常 `--duration` 期限或 Ctrl+C 結束整組。有限觀測中某條提前停止但其餘持續至期限時正常退出，提前停止原因仍保留於畫面。

### 工作台操作偏好

本機主選單以 S 開始監看、L 查看本工作台最近一次錄製、5 瀏覽、A 進階工具。原數字入口保留相容；遠端選單不讀寫此偏好。

| 參數／檔案 | 格式及限制 |
| --- | --- |
| `menu --preferences-file` | 預設目前目錄 `.uart-workbench.local.json`；最多 8192 bytes，不接受符號連結 |
| 偏好 JSON | version 整數 1、config 設定檔路徑、recording 為 inherit／on／off，可另含 profile 端點配置路徑；路徑最多 4096 個可顯示字元，拒絕未知欄位 |
| `.uart-workbench-<隨機值>.local.toml` | S→D→4 自動建立的新端點 Profile，沿用原格式／保存／重新枚舉規則；放在偏好檔同目錄，不覆蓋舊配置 |
| 搜尋命中附近區段 | 預設前後各 30 秒，W 可調成 0～3600 秒；固定命中的 Epoch，納入關鍵字起始及完成區塊時間 |
| F 的相對時間 | 非負分鐘，可小數，最多 525600 分鐘；以 Channel 首筆有效紀錄時間為起點，不是選定 Epoch 起點；完整 ISO 8601 仍支援 |
| 自動文字輸出 | `<錄製根目錄>/exports/uart-<UTC日期時間>-<隨機識別>.txt`；Enter 使用建議路徑，M 或直接輸入可自訂；原 x 模式拒絕覆蓋 |

偏好只在使用者確認開始時保存，只有上列無秘密欄位；不保存憑證、Capture、歷史就緒狀態或搜尋結果。資料損壞／未知格式時本次不保存，不覆蓋原檔；保存失敗明示但不妨礙本次明確啟動。只讀載入不啟動 UART，重用仍須核對原全部裝置條件。自選端點 Profile 僅 read；可人工 RX 恢復的內建方案或私人設定則沿用既有 configure 權限，不新增 tx。

L 位置僅來自本次 Launcher 在全部就緒及錄製啟動後回報的 instance／通道，保留在記憶體，不用最新目錄或磁碟排序推測。資料流導覽最多沿用 1024 個 Epoch 與 256 個邊界的摘要上限，完整性檢查仍涵蓋全檔。收尾預覽與路徑詳情不等同完整性驗證。

### 依 Gateway 設定整合啟動

`uart start --config <GatewayTOML>` 或本機工作台選 6，沿用完整 Gateway 設定格式。與 `local --profile` 的端點 Profile 不可互換。此入口目前僅支援一個 Principal、1～2 個不同端點、全部目標 read 或 read＋configure；拒絕 tx。通道須 auto_open_rx=true、auto_reconnect=false，UART 維持 115200／8N1／無流控、DTR／RTS false。其他合法 Server 設定仍可走獨立 Gateway。

| 參數／來源 | 行為 |
| --- | --- |
| `--config` | 必填，UTF-8／BOM TOML，最多 64 KiB，原檔不改寫 |
| `network.port` | 使用完整設定的 TCP Port（省略為 18081）；此入口拒絕非預設全域 `--port`，請修改私人設定 |
| `--record-dir` | 本次啟用錄製，沿用其餘錄製容量限制並覆寫目錄；與 --no-record 互斥 |
| `--no-record` | 本次停用錄製；兩者都省略時沿用設定 |
| `--startup-timeout` | 預設 20 秒，有限數且大於 0、不超過 300 秒，全部目標共用期限 |
| `auth.principals[].token_secret_ref` | 沿用 env:／file: 參照；僅標準 env:UART_GATEWAY_TOKEN 未設定或空字串時生成臨時 Token，不保存值、不置於 argv |

缺少自訂環境變數或 Token 檔案時失敗，不改用其他憑證。暫存 TOML 只有原秘密參照，子程序環境取得臨時 Token；單一工作區 SDK 使用相同值。臨時憑證不提供給其他終端；需要另開 Client 或 SSH 遠端時，請預先設定共用秘密參照或沿用獨立 Gateway。Selector 未含 Port 時，啟動前須唯一枚舉解析，暫存副本固定這次端點，再由原 Backend 核對全部條件；不自動移除已指定的 Port 限制。

### 錄製搜尋與區段參數

| 命令／參數 | 預設與限制 |
| --- | --- |
| `recording-search <Channel目錄> --keyword <文字>` | UTF-8 字面、不重疊、區分大小寫，1～4096 UTF-8 bytes 可顯示文字；結果控制字元跳脫 |
| `--max-results`（搜尋） | 預設 200，整數 1～1000；再發現命中時停止比對，仍完成整份完整性掃描 |
| `--epoch`（搜尋／回看／匯出） | 精確 Epoch，1～128 個可顯示字元；省略為全部 |
| `--from-time`／`--to-time`（搜尋／回看／匯出） | ISO 8601，必含日期、時間及 Z 或明確時區，最長 64 字元；轉 UTC 比較，包含兩端；起不得晚於迄 |

條件以交集套用於 RX 整塊，不改錄製格式或線上 API；無條件時維持既有全檔回看／匯出。結果標示選取量與整份資料量，整份有缺口／損壞／未收尾仍退出 2，不能用篩選消除警告；操作失敗退出 1。離線工具無需 Token，不建立 Gateway 連線。

### 伺服器參數基線

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `gateway.instance_name` | string | 建議 `uart-gateway` | 1–64 UTF-8 bytes | 可省略 | 人類識別名稱 | 不當作認證身分或 Instance ID |
| `gateway.max_channels` | integer | 建議 4 | 1–64 | 可省略 | 限制設定通道總數 | Phase 1 實體只驗證 1 個；上限非效能保證 |
| `gateway.lifecycle_timeout_ms` | integer | 建議 10000 | 1000–60000 | 可省略 | 開啟、關閉與 Binding 操作的協調等待期限 | 逾時不代表底層已停止；晚到回報不能恢復舊 TX 資格 |
| `network.bind_host` | string | 確認 Loopback；候選 `127.0.0.1` | V1 僅 `127.0.0.1` | 可省略 | Gateway 接聽位址 | 禁止默認 `0.0.0.0`；其他拓撲需新評估 |
| `network.port` | integer | 建議 18081 | 1024–65535 | 可省略 | Structured Protocol（結構化協定）Port | 必須檢查衝突與整條 Forward（轉送）對應 |
| `network.max_clients` | integer | 建議 8 | 1–128 | 可省略 | 包含未認證連線的總上限 | 防止握手前耗盡資源 |
| `network.max_frame_bytes` | integer | 建議 65536 | 16384–65536，含結尾 LF | 可省略 | Frame（訊框）上限 | 必須在收完整行前執行限制 |
| `network.max_pending_requests` | integer | 建議 32 | 1–256／Session | 可省略 | 並行請求上限 | 超限回覆錯誤或關閉濫用連線 |
| `network.max_subscriptions` | integer | 建議 16 | 1–128／Session | 可省略 | 訂閱總上限 | 包含同 Channel 的多個訂閱 |
| `network.handshake_timeout_ms` | integer | 建議 5000 | 1000–30000 | 可省略 | TCP 接受至認證完成的總期限 | 不能藉反覆 Hello 延長 |
| `network.heartbeat_interval_ms` | integer | 建議 5000 | 1000–30000 | 可省略 | SDK Ping（存活詢問）間隔 | 與 SSH Keepalive（連線檢查）分開 |
| `network.idle_timeout_ms` | integer | 建議 15000 | 3000–120000，至少心跳間隔 3 倍 | 可省略 | 無有效入站訊息時關閉 Session | 單純 Server 持續輸出不代表 Client 存活 |
| `network.max_json_depth` | integer | 建議 16 | 4–32 | 可省略 | JSON 巢狀深度上限 | 解析前後均需受 Frame 上限保護 |

SSH 的 Host、帳號、金鑰與 Forward 屬部署層，不由 Gateway 設定自動修改。候選 Port 對應見 [部署文件](deployment.md)。

Raw TCP（原始串流）目前不屬 Phase 1 設定 Schema（結構），不建立未使用的啟用開關；Q-06／Q-07 核准需要相容介面後才擴充設定。

## Channel 與 Serial

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `channels[].id` | string | 無 | ASCII `[A-Za-z0-9_-]`，1–64 字元，全域唯一 | 必填 | 穩定 Channel ID | 不從 COM 名稱或 Label（顯示名稱）動態產生 |
| `channels[].label` | string | 無 | 1–128 UTF-8 bytes，有效 Channel 間唯一 | 必填 | 顯示目前目標 | 只是描述；重啟不自動代表已確認接線 |
| `channels[].target_note` | string 或 null | 建議 `null` | 最多 512 UTF-8 bytes | 可省略 | 板子、版本等備註 | 避免敏感資訊；Capture（擷取紀錄）存快照 |
| `channels[].adapter.port` | string | 首次目標 COM3；無通用預設 | 有效 Windows Serial Endpoint（序列端點） | 無唯一硬體條件時必要 | 明確選擇目前端點 | COM 名稱可被重用；不視為永久 ID |
| `channels[].adapter.vid`／`pid` | integer 或 null | 建議 `null` | 0–65535；兩者成對 | 可省略 | USB 裝置類型條件 | 同型 Adapter 可能完全相同 |
| `channels[].adapter.serial_number` | string 或 null | 未知 | 1–128 UTF-8 bytes 或 null | 可省略 | 優先硬體身分線索 | 缺失／重複時不自動選第一個 |
| `channels[].adapter.location` | string 或 null | 建議 `null` | 1–256 UTF-8 bytes 或 null | 可省略 | USB 位置輔助條件 | 換插孔會改變，不證明板子身分 |
| `channels[].serial.baudrate` | integer | 確認 115200 | 300–4000000 且 Driver 支援 | 可省略 | Baud Rate（鮑率） | Phase 1 只承諾驗證 115200；其他值需實測 |
| `channels[].serial.bytesize` | integer | 確認 8 | 5、6、7、8 且平台支援 | 可省略 | 資料位元 | 配合板子設定 |
| `channels[].serial.parity` | string | 確認 `none` | `none/even/odd/mark/space` 且平台支援 | 可省略 | 同位檢查 | 不支援時明確拒絕 |
| `channels[].serial.stopbits` | number | 確認 1 | 1、1.5、2 且平台支援 | 可省略 | 停止位元 | 不悄悄替換成其他設定 |
| `channels[].serial.flow_control` | string | 建議 `none` | `none/rtscts/xonxoff` | 可省略 | 流量控制 | 未接線不能任意啟用 RTS/CTS |
| `channels[].serial.dtr`／`rts` | boolean | 建議 `false`，實機前待確認 | `true/false` | 可省略但硬體前須確認 | 初始控制線意圖 | Driver 開啟脈衝無法由設定完全保證消除 |
| `channels[].serial.read_timeout_ms` | integer | 建議 20 | 1–1000 | 可省略 | 有限等待 RX | 逾時不是拔除訊號 |
| `channels[].serial.write_timeout_ms` | integer | 建議 1000 | 10–30000 | 可省略 | 有限等待 Driver 寫入 | 逾時可能是部分寫入，不自動重送 |
| `channels[].auto_open_rx` | boolean | 建議 `true` | `true/false` | 可省略 | 啟動時可先觀測 | 不自動確認 Binding 或允許 TX |
| `channels[].auto_reconnect` | boolean | 建議 `false` | `true/false` | 可省略 | 失去裝置後重開 | 啟用仍需唯一可信裝置匹配；不得只憑同名 COM |
| `channels[].reconnect_max_delay_ms` | integer | 建議 30000 | 1000–300000 | 可省略 | 裝置重試退避上限 | 不用於 TX 重送 |

Selector（裝置選擇條件）存在多個條件時採交集，不是依序放寬；多個符合或條件矛盾都必須停止。若希望以唯一硬體序號追蹤 COM 重編號，不同時固定舊 Port 名稱；明確指定 Port 就是限制該端點。Phase 1 不設讓使用者關閉「換板使舊 TX 失效」的開關。候選 Restart（重啟）政策固定要求再次 Confirm（確認），是否採用由 Q-05 核准。

## Authentication 與 Authorization

Authentication（認證）決定 Principal（身分），Authorization（授權）決定可操作的 Channel 與能力。

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `auth.principals[].id` | string | 無 | ASCII `[A-Za-z0-9_-]`，1–64 字元，唯一 | 必填 | 稽核身分 | 不接受 Client 自訂來覆蓋 |
| `auth.principals[].token_secret_ref` | string | 無 | 明確、可讀的受保護秘密參照 | 必填 | 載入 Token（認證憑證） | 不將真值寫入 TOML、命令列或 Log |
| `auth.principals[].channels` | array[string] | 無 | 已設定 Channel ID 的非空清單 | 必填 | 限制可存取通道 | V1 不隱含萬用字元授權 |
| `auth.principals[].permissions` | array[string] | 無 | `read/tx/configure` 非空集合 | 必填 | 限制操作種類 | 候選 `tx`、`configure` 必須同時具備 `read` |
| `auth.allow_anonymous` | boolean | 確認 `false` 原則 | 僅 `false` | 可省略 | 禁止未認證操作 | SSH Tunnel 不能代替認證 |

候選 Token 為至少 32 bytes 隨機值的 Base64url 編碼；傳輸字串不含 padding（補齊字元），長度 43–128 ASCII 字元。秘密儲存、輪替與每工具憑證政策須經 Q-08 核准。無有效 Principal 時啟動失敗，不自動進入匿名模式。

## Buffer、Queue 與 Payload

Payload（負載）上限以下皆指解碼後原始 bytes。數值均為候選值，線上宣告及錯誤語意見 [協定 V1](protocol/v1.md)。

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `buffer.rx_ring_bytes` | integer | 建議 4194304／Channel | 65536–67108864 | 可省略 | Replay 保留範圍 | 不保證固定分鐘數或硬體零遺失 |
| `buffer.rx_handoff_bytes` | integer | 建議 262144／Channel | 4096–4194304 | 可省略 | Reader 到 Event Loop 交接 | 溢位須回報已知缺口或終止連續性 |
| `buffer.subscription_queue_bytes` | integer | 建議 262144／Subscription（訂閱） | 4096–4194304 | 可省略 | 慢 Client 隔離 | 不允許引用舊 Chunk（資料區塊）而繞過記憶體上限 |
| `buffer.connection_queue_bytes` | integer | 建議 1048576／Connection（連線） | 65536–8388608 | 可省略 | 序列化輸出上限 | 控制訊息保留空間；仍滿時關閉 |
| `buffer.tx_queue_items` | integer | 建議 16／Channel | 1–256 | 可省略 | 待執行 TX 數量 | 不包括正在執行的一筆；清理過期 Owner 工作 |
| `buffer.tx_queue_bytes` | integer | 建議 65536／Channel | 4096–1048576 | 可省略 | 待執行 TX 總量 | 項目與 bytes 上限任一達到即拒絕 |
| `payload.max_rx_chunk_bytes` | integer | 建議 4096 | 1–4096 | 可省略 | RX 訊息粒度 | 不代表文字行或 Shell 回覆邊界 |
| `payload.max_tx_bytes` | integer | 建議 4096 | 1–4096 | 可省略 | 單次 TX 上限 | 不自動切成可與其他 Owner 交錯的多次操作 |
| `payload.rx_batch_delay_ms` | integer | 建議 10 | 1–100 | 可省略 | 低延遲與批次效率折衷 | 不是物理 UART Timestamp（時間戳）精度保證 |

32-bit／64-bit 平台上的實際物件成本另算。Queue 需同時限制 Payload 與 Metadata（中繼資料）總量；不能只限制 bytes 卻允許無限個空項目。

## Lease、TX 結果與 Client

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `lease.ttl_ms` | integer | 建議 15000 | 3000–120000 | 可省略 | 租約有效期間 | 以 Server Monotonic Clock（單調時鐘）判斷 |
| `lease.renew_interval_ms` | integer | 建議 5000 | 1000 至 TTL／3 | 可省略 | SDK 更新建議間隔 | 延遲後不得假設仍有效 |
| `tx.result_retention_ms` | integer | 建議 600000 | 10000–86400000 | 可省略 | 最終結果快取保留 | 只在同一 Instance 有效，不是永久去重 |
| `tx.result_cache_entries` | integer | 建議 4096／Gateway | 16–65536 | 可省略 | 有界 TX 紀錄總數 | 已接受紀錄在保證期間內不可提早淘汰；滿時拒絕新 TX |
| `client.host` | string | 建議 `127.0.0.1` | Phase 1 為本地 Forward 位址 | 可省略 | Client 連線位置 | 不預設可直連 Windows |
| `client.port` | integer | 建議 18081 | 1024–65535 | 可省略 | 本地 Forward Port | 必須符合部署設定 |
| `client.token_secret_ref` | string | 無 | 受保護秘密參照 | 必填 | 認證資料 | 不從 UART Log 或不可信文字讀取秘密 |
| `client.request_timeout_ms` | integer | 建議 5000 | 1000–60000 | 可省略 | 一般請求等待期限 | 本地 Timeout 不取消已接受 TX，也不表示未執行 |
| `client.reconnect_initial_ms` | integer | 建議 500 | 100–10000 | 可省略 | 連線重建初始退避 | 加入抖動；不自動重送有副作用請求 |
| `client.reconnect_max_ms` | integer | 建議 30000 | 初始值至 300000 | 可省略 | 退避上限 | 認證／版本錯誤停止無效重試 |

Server 不接受 Client 任意指定 Lease TTL；以握手公布值為準。SDK 的慢速接收佇列也需有界，候選沿用 Subscription Queue 上限並回報本地缺口，不默默刪除資料。

## Recording 與 Audit

### 本輪已實作的 RX 錄製參數

預設關閉；手動 Gateway 的 TOML 加上 `[recording]` 可啟用，Fake／Serial 模式共用。`uart local --record-dir 路徑` 將路徑轉為絕對路徑，再產生這組既有伺服器設定；不將錄製目錄持久寫入本機端點 Profile。遠端 Client 不可透過 API 啟停錄製，檔案保存在 Gateway 所在電腦。

| 欄位 | 預設／限制 |
| --- | --- |
| `recording.enabled` | false，必須是布林值；停用時拒絕其餘錄製欄位，避免誤認啟用 |
| `recording.directory` | 啟用時必填，絕對路徑字串；每次每通道建立新 Capture，不覆寫 |
| `recording.queue_bytes` | 1048576；65536～16777216；另固定 1024 項上限，包括正在寫入的資料 |
| `recording.segment_bytes` | 67108864；65536～67108864；包含 Header／Footer |
| `recording.max_capture_bytes` | 1073741824；分段上限～1073741824；本次單通道 Capture 的所有分段實際 bytes 合計上限 |

容量或磁碟失敗停止該錄製並使用既有 `RECORDING_IO_FAILED` 原因，佇列滿使用 `RECORDING_OVERFLOW`；詳細中文原因放 Gateway 診斷。原 RX 及其他通道繼續。Launcher 明確要求錄製卻啟動失敗時會顯示錯誤並清理本次程序，避免誤以為已保存。Runtime 失敗由 CLI 顯示健康事件，不自動重試或清除檔案。

實際格式及完整性以 [錄製規格](recording.md) 為準。本輪不提供年齡淘汰或跨 Capture 的磁碟總量管理，**不接受**下表候選的 `retention_days/max_bytes_per_channel/include_tx_payload`；未知欄位仍拒絕。

### 尚未全部實作的保留與稽核基線

Recording（UART 錄製）與 Audit（操作稽核）分開。Recorder 失敗不阻塞 RX，但使 Capture 不完整。

| 參數名稱 | 型別 | 預設值／狀態 | 合法範圍 | 必要性 | 用途 | 安全性注意事項 |
| --- | --- | --- | --- | --- | --- | --- |
| `recording.enabled` | boolean | 建議 `false`，Q-09 待核准 | `true/false` | 可省略 | 是否常駐錄製 | 關閉時長時間斷線可能超過 Ring 保留範圍 |
| `recording.directory` | string | 無 | 本機可寫的絕對路徑 | 啟用時必要 | 存放 UART 紀錄 | 不放版本庫；使用最小檔案存取權限 |
| `recording.queue_bytes` | integer | 建議 1048576／Channel | 65536–16777216 | 可省略 | 有界磁碟工作佇列 | 溢位回報，不宣稱完整 |
| `recording.segment_bytes` | integer | 建議 67108864 | 1048576–1073741824 | 可省略 | 錄製分段上限 | 含 Metadata，須小於等於每通道容量上限 |
| `recording.retention_days` | integer | 建議 7 | 1–365 | 可省略 | 已關閉分段的年齡上限 | 保留政策待機密性確認 |
| `recording.max_bytes_per_channel` | integer | 建議 1073741824 | 67108864–1099511627776 | 可省略 | 每通道總容量上限 | 包含活動分段；刪除舊分段仍不足時停止錄製並告警 |
| `recording.include_tx_payload` | boolean | 建議 `false` | `true/false` | 可省略 | 是否保存完整 TX | 命令可能含帳密 |
| `audit.enabled` | boolean | 建議 `true` | `true/false`，停用需明確政策 | 可省略 | 身分、Lease、Binding、TX 狀態稽核 | 不保存 Token；日誌本身也需容量上限 |
| `audit.max_bytes` | integer | 建議 67108864／Gateway | 1048576–1073741824 | 可省略 | 稽核輪替總上限 | 滿載或不可寫需顯示健康狀態，不能無聲丟棄 |

保留時同時滿足年齡與總容量條件，優先淘汰最舊的已關閉分段，不刪除目前活動分段來偽裝正常。Phase 1 須補足錄製檔格式、輪替與非正常關閉標記的實作契約；本文件不代表已存在可讀取檔案。

## 跨欄位驗證與失敗行為

啟動時拒絕重複 Channel ID／Label、互斥設定、無效範圍、不存在的 ACL Channel、沒有憑證、無界 Queue、可能產生超限 Frame 的組合，以及 Recording 分段大於容量上限。Frame 上限是序列化後 UTF-8 bytes 含 LF；Channel 清單需依協定分頁，不用截斷 JSON 解決超限。

裝置不存在或 Port 占用可作 Channel 故障呈現，其餘有效 Channel 繼續運作；結構性設定錯誤則拒絕啟動。實際採用值必須在 Hello／Channel Snapshot（快照）中依協定揭露；不得記錄憑證或私密路徑內容。
