# RX 錄製格式與生命週期

## 本輪設計

使用者已授權只讀 RX 錄製。沿用 ADR-006：錄製在 Gateway，每通道一個有界非同步 Writer，不依賴 Client 訂閱、顯示模式或 SSH Tunnel；Core 只提供同步、不可阻塞的事件觀察回呼，檔案與協定轉換由 Server 負責。預設關閉，`uart local --record-dir 路徑` 明確啟用；手動 Gateway 使用 `[recording]`。不新增 UART 傳送或控制線操作。

每次 Gateway 在指定目錄下建立唯一的 `capture-<instance>/<channel_id>`，不覆寫舊資料。各通道分段寫入 `000001.jsonl` 等 UTF-8 JSON Lines。記憶體佇列上限 1024 項及 1 MiB（包含寫入中資料），預設每段 64 MiB、每次每通道合計 1 GiB，包含 Metadata／Footer。達上限時停止該通道錄製，透過健康事件告警，並使用預留空間寫入標示不完整的 Footer。

## 跨次錄製保留管理

使用 `uart recording-retention <錄製根目錄> --days 7 --max-bytes 1073741824` 預覽。可只指定一項條件，兩項同時指定時先依期限移除，再依總量從最舊的 Capture 開始。核對候選清單後，以原參數加 `--apply <plan>` 套用。計畫改變時須重新預覽。清理只處理具有完整收尾結構、可驗證雜湊的 Capture；錄製中、已知不完整或不符合格式的目錄保留。受保護 Capture 可能使容量目標無法達成，結果會顯示 `target_exceeded`。掃描採有界中繼資料；套用才逐份檢查雜湊及原始完整性。

清理前先在同一錄製根目錄將目標移入 `.retention-…` 暫存名稱，再移除已核對的分段。作業中斷時，預覽會列出這些暫存目錄供人工檢查。保留操作由操作者明確觸發。

## 檔案格式 V1

每行為獨立 JSON。欄位識別使用英文；時間為 UTC，位移／序號為十進位字串；錄製格式版本與 API 版本分開。

- 第一行 `type=segment_start`：`recording_format_version=1`、`capture_id`、`gateway_instance_id`、`channel_id`、`segment`、`timestamp_utc`、`channel` 快照。快照保存當時名稱、Adapter、UART、Binding 與 Epoch，不包含 Principal、Token、命令列或 TX Payload。
- `type=channel_state`：`timestamp_utc`、`channel` 當時快照。包含開啟、Epoch／Binding 改變、裝置遺失及關閉；`stream_epoch=null` 的關閉／遺失狀態不會把舊資料改成新世代。
- `type=rx`：`timestamp_utc`、`binding_revision`、`stream_epoch`、`seq`、`offset`、`next_offset`、`data_b64`。原始 bytes 不經 UTF-8／顏色轉換，無 Client 訂閱也只記一次，即時重播不再次錄製。
- `type=gap`：已知位移不連續的 `stream_epoch/from_offset/to_offset`；標示錄製不完整，不以未知長度填成精確缺口。
- 最後一行 `type=segment_end`：`final`、`integrity`、`reason_code`、`timestamp_utc`、`sha256`。雜湊涵蓋該段 Footer 前全部原始行（含 LF），用於偵測損壞，不能當作防竄改認證。`final=false` 只是輪替，唯有最後一段 `final=true` 才代表這次 Capture 正常收尾。

裝置遺失使連續性 `unknown`，磁碟錯誤、錄製佇列溢位或已知缺口為 `incomplete`；狀態只能保守惡化。正常結束且無已知缺口為 `no_known_gap`，只涵蓋 Gateway 已觀測的資料。沒有最終 Footer、部分行、雜湊不符等不可稱為完整；強制停止可能連最後的錯誤紀錄都寫不出。

Writer 在工作執行緒寫入、flush；正常結束亦 fsync，沒有逐筆掉電持久性保證。OS 若卡住磁碟 I/O，應用層不能安全強殺執行緒；正常清理會等已開始工作收斂，Launcher 仍有原有子程序停止期限。異常失敗會透過 stderr、既有 ChannelSnapshot.recording 及 recording_state 事件回報，不能只依賴同一顆失敗磁碟。

已知刷新失敗時會盡可能移除未確認的 Footer；若磁碟完全失效而不能改寫，必須以外部錄製失敗訊息為準，即使日後檔案檢查沒有發現缺口。正常 Footer 與雜湊不能證明掉電持久性。

`uart recording-info <Channel目錄>` 離線驗證分段順序、版本、Capture 身分、SHA-256、RX Base64／位移／序號及最後收尾；有缺口、遺失、損壞或未完成標記時，回報 incomplete 或 unknown，退出碼 2；無已知缺口退出 0，操作失敗退出 1。檢查不重播資料到 UART，也不解讀或執行終端控制字元。請在錄製結束後執行。

### recording-info 的 Epoch 摘要（2026-11-07）

原命令現在直接列出通道／Capture／Instance、每個 Epoch 的首筆 offset、末筆 next_offset（右端點不含）、RX 區塊及解碼後 bytes。位移跨度不當作資料量；有缺口時仍計算實際可解析 payload。相同 Epoch 跨正常分段接續加總，不因分段 Header 重複計數；ready 快照已有 Epoch 但尚無 RX 時列出零區塊及「無 RX」，不假造起點。

摘要另依檔案順序顯示資料流建立、Epoch 切換、裝置遺失／故障、開啟／關閉及已知缺口；保留關聯 Epoch。Epoch 切換本身不能證明是手動重開或 Confirm，應搭配狀態與當時操作判讀。這些數字是資料摘要，不是各 Epoch 的獨立完整性認證；整份原有 SHA-256、序號／位移與 Footer 檢查仍為判讀依據。損壞、缺 Footer 或裝置遺失時，可解析部分仍有摘要，但警告與退出碼不被清除。

同一次原檢查器掃描透過同步回呼聚合，不另讀整份 Capture、不保留 UART payload。最多列出 1024 個 Epoch 與 256 筆狀態／世代事件，摘要識別文字最多 128 字元。達上限時明示摘要未完整列出，仍掃描及驗證所有分段，並計算未列 RX 區塊／bytes；已列與未列加總共同核對原 RX 總計。只省略摘要不會把健康錄製標為損壞；核對不一致則標為 incomplete。非法格式、混合 Capture、雜湊不符等仍依原檢查器處理，不猜測不可解析的資料量。

無需 Token、Gateway 或實體 UART；原始錄製與其他檔案不改寫。工作台「瀏覽錄製→檢查」沿用此命令，因此也顯示同樣摘要。錄製格式與線上 API 沒有改動；既有 inspect_capture 與文字匯出／回看回呼介面保留，只有摘要入口額外彙整資料。

## 可讀文字匯出

`uart recording-text <Channel目錄> --output uart-readable.txt` 以同一檢查器逐行驗證並匯出所有分段，使用有界的單行／單區塊暫存，不將整份 Capture 載入記憶體。輸出檔必須是新檔，拒絕 `.jsonl` 避免混入原分段；發生寫入錯誤會保留可能的部分文字，缺少「匯出完成」摘要時不可視為已完成。

每段輸出含 UTC 時間、Epoch、原 RX 區塊 `[offset,next_offset)`。增量 UTF-8 解碼跨區塊及正常輪替延續；尚未完成的字元待下一塊顯示，完成時標示「含前區塊續字元」，原區塊位移仍指當下 RX 紀錄。遇到缺口、格式錯誤或 Epoch 改變會清空解碼狀態，尾端不完整字元以跳脫 bytes 顯示，避免拼接錯誤中文字。不累積沒有換行的長 UART 行，文字可能依 RX 區塊拆成多個帶標記的畫面行。

僅 LF／TAB 與可顯示字元直接保留，CR、退格、ANSI 控制碼及非 UTF-8 bytes 以 `\r`、`\x08`、`\x1b`、`\xff` 等文字顯示。無法自動判定所有二進位或其他文字編碼，匯出的文字是檢視副本，原 JSONL 是還原 bytes 的來源。

輸出包含通道快照、狀態／缺口與最終完整性摘要。損壞或未正常收尾時仍保存已讀出的內容，明示警告並退出 2；無已知缺口退出 0，參數／輸出失敗退出 1。雜湊結果在分段尾端才可確認，因此中途文字不能當作已驗證的完整資料。請在錄製停止後執行；不需要 Token 或 UART 裝置。

## 離線回看設計（2026-11-06）

`uart recording-play <Channel目錄> [--terminal]` 使用原有檢查器逐筆掃描及 TerminalRenderer，快速依序呈現 RX，不按原時間等待。預設跳脫控制字元；明確指定 `--terminal` 才呈現 CR／LF／BS／TAB 與允許的 SGR 顏色，且 stdout 必須是互動式終端。診斷放 stderr，開始及結束明示離線回看；這不是即時 RX 或送資料回 UART。

UTF-8／ANSI 解析狀態跨正常分段延續，遇到缺口、Epoch 變更、裝置遺失或格式異常時收尾並重設。正常結束或中止時重設顏色。資料逐筆寫入終端，慢終端使離線掃描放慢，不增加無界記憶體 Queue；Ctrl+C 通知掃描工作停止並等待已開始的 I/O 收斂。底層檔案／終端 I/O 若卡住，仍無法安全強殺執行緒。

檢查與顯示在同一次掃描中完成，分段雜湊讀到 Footer 才能確認。畫面先明示尚未完成驗證，最終以摘要為準：無已知缺口退出 0，不完整／未知退出 2，操作失敗退出 1，中止退出 130 且不宣稱檢查完成。不修改來源、不要求 Token 或建立 Gateway 連線。

## 搜尋與區段回看（2026-11-07）

`recording-play`、`recording-text` 與新 `recording-search` 共用 RXSelection，接受 Epoch／起迄時間交集。選取粒度為整個 RX 區塊，非對 UART 字元時間做內插；時間兩端包含、時區正規化為 UTC。時間不合法或缺少時區的篩選參數直接失敗；紀錄本身沒有有效時間時，不納入時間篩選。篩選範圍若從半個 UTF-8 或 ANSI 序列開始，不推測範圍外狀態，解碼器及終端過濾器按原規則處理。

每次仍使用 inspect_capture 掃完整 Capture，驗證所有分段／雜湊／位移與收尾；RX 被排除時先收尾並重設解碼器，不跨排除區塊拼接文字。狀態／缺口／Header 診斷保留整份範圍。回看／匯出摘要分別標明整份完整性與選取 RX 區塊／bytes；空選取也必須完成原掃描，不代表原錄製健康。

`recording-search` 在原檢查器回呼中比對 UTF-8 bytes，可跨連續區塊及正常分段。不跨 Epoch、Binding Revision、位移缺口、狀態紀錄或排除區塊；不重疊命中。每筆列出 Epoch、完成命中的區塊時間、原 byte 位移 `[offset,next_offset)`，以及截短並跳脫控制字元的文字預覽。跨區塊關鍵字的位移是其真實 byte 起迄，時間不是字元精準時間。非 UTF-8 不猜其他編碼，ANSI 序列插入關鍵字中也不視為連續字面命中。

搜尋僅保留最多關鍵字長度減一的續接 bytes、最多指定上限的結果、各 128 字元的時間／Epoch 摘要及有界文字預覽；達結果上限後仍繼續掃描完整性，明示未列出全部命中。Ctrl+C 會通知掃描停止並等待已開始工作結束；中止不能宣稱完成檢查。原 JSONL 不修改，文字匯出仍只建立新檔；詳細參數與上限以 [設定規格](configuration.md#錄製搜尋與區段參數) 為準。

## 工作台導覽補充（2026-11-07）

搜尋結果新增關鍵字起始區塊的時間，原完成區塊時間與 byte 位移不變；跨區塊比對以 deque 保留最多續接 bytes 所涉及的區塊時間跨度，長度由 4096-byte 關鍵字上限約束，不累積整份資料。命中附近回看固定相同 Epoch，時間範圍納入兩端區塊再擴張預設前後秒數；時間或識別無效時不猜測。

工作台 F 的時間／資料流清單共用 EpochSummary 與 inspect_capture，同一次掃描讀完整份 Capture；只保留摘要、首筆有效紀錄時間與最多 1024 個 Epoch 的首筆 RX 時間，無 UART payload 快取。首筆有效紀錄通常是初始 Header，Footer 不作為起點；缺時間時依檔案順序取第一筆可解析紀錄，全部缺時間則拒絕相對分鐘，仍可依 Epoch 操作。取消會通知並等待掃描工作結束。

分組與 L 捷徑不改變正常收尾或連續性判定。自動匯出在錄製根目錄的 exports 下建立新文字檔，來源 JSONL 不變；命中片段／相對範圍仍完整檢查原 Capture，保留範圍外損壞、裝置遺失與收尾警告。

## 與線上 API 的界線

使用 V1 已定義的 recording_state 與 Snapshot 欄位，不更動 Wire Contract。關閉功能時仍為 disabled/not_recording。這不是 Audit、磁碟 Replay API、自動重連或常駐錄製的完整保留政策。實體 Win11 單 UART 錄製、雙 UART 與 30 分鐘接收仍需個別驗證。
