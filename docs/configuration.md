# 設定規格

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11～Q-14、Q-17 的現場資訊仍待取得。Phase 1 尚未開始。

## 狀態、名稱與讀法

本文件是設定參數的唯一權威規格草案，尚無載入器、設定檔或 `pyproject.toml`。建議使用 TOML，格式選擇與下列候選值仍待核准。表中的點號表示設定路徑，`channels[]` 表示 Channel（邏輯通道）清單；不是線上 JSON 欄位的另一套定義。

「確認」只表示使用者已明示；「建議」不代表已核准。`必填` 不提供猜測值；`可省略` 採表中候選預設；`條件必要` 依說明判斷。所有容量均有上限，不允許 `0` 表示無限。秘密只透過參照載入，不在文件或版本庫放真值。

所有設定在 Phase 1 候選方案中於啟動載入；不提供任意熱更新。Binding（綁定）確認是經授權的執行期操作。變更 Serial 設定需停止舊操作並建立新 Epoch（資料流世代），不是直接覆寫欄位。

## Gateway 與網路

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
