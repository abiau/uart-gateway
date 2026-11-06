# RX 錄製格式與生命週期

## 本輪設計

使用者已授權只讀 RX 錄製。沿用 ADR-006：錄製在 Gateway，每通道一個有界非同步 Writer，不依賴 Client 訂閱、顯示模式或 SSH Tunnel；Core 只提供同步、不可阻塞的事件觀察回呼，檔案與協定轉換由 Server 負責。預設關閉，`uart local --record-dir 路徑` 明確啟用；手動 Gateway 使用 `[recording]`。不新增 UART 傳送或控制線操作。

每次 Gateway 在指定目錄下建立唯一的 `capture-<instance>/<channel_id>`，不覆寫舊資料。各通道分段寫入 `000001.jsonl` 等 UTF-8 JSON Lines。記憶體佇列上限 1024 項及 1 MiB（包含寫入中資料），預設每段 64 MiB、每次每通道合計 1 GiB，包含 Metadata／Footer；達上限停止該通道錄製，明確回報不完整。此切片不自動刪除舊 Capture，跨啟動的年齡／總容量保留管理仍待後續實作。

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

## 可讀文字匯出

`uart recording-text <Channel目錄> --output uart-readable.txt` 以同一檢查器逐行驗證並匯出所有分段，使用有界的單行／單區塊暫存，不將整份 Capture 載入記憶體。輸出檔必須是新檔，拒絕 `.jsonl` 避免混入原分段；發生寫入錯誤會保留可能的部分文字，缺少「匯出完成」摘要時不可視為已完成。

每段輸出含 UTC 時間、Epoch、原 RX 區塊 `[offset,next_offset)`。增量 UTF-8 解碼跨區塊及正常輪替延續；尚未完成的字元待下一塊顯示，完成時標示「含前區塊續字元」，原區塊位移仍指當下 RX 紀錄。遇到缺口、格式錯誤或 Epoch 改變會清空解碼狀態，尾端不完整字元以跳脫 bytes 顯示，避免拼接錯誤中文字。不累積沒有換行的長 UART 行，文字可能依 RX 區塊拆成多個帶標記的畫面行。

僅 LF／TAB 與可顯示字元直接保留，CR、退格、ANSI 控制碼及非 UTF-8 bytes 以 `\r`、`\x08`、`\x1b`、`\xff` 等文字顯示。無法自動判定所有二進位或其他文字編碼，匯出的文字是檢視副本，原 JSONL 是還原 bytes 的來源。

輸出包含通道快照、狀態／缺口與最終完整性摘要。損壞或未正常收尾時仍保存已讀出的內容，明示警告並退出 2；無已知缺口退出 0，參數／輸出失敗退出 1。雜湊結果在分段尾端才可確認，因此中途文字不能當作已驗證的完整資料。請在錄製停止後執行；不需要 Token 或 UART 裝置。

## 離線回看設計（2026-11-06）

`uart recording-play <Channel目錄> [--terminal]` 使用原有檢查器逐筆掃描及 TerminalRenderer，快速依序呈現 RX，不按原時間等待。預設跳脫控制字元；明確指定 `--terminal` 才呈現 CR／LF／BS／TAB 與允許的 SGR 顏色，且 stdout 必須是互動式終端。診斷放 stderr，開始及結束明示離線回看；這不是即時 RX 或送資料回 UART。

UTF-8／ANSI 解析狀態跨正常分段延續，遇到缺口、Epoch 變更、裝置遺失或格式異常時收尾並重設。正常結束或中止時重設顏色。資料逐筆寫入終端，慢終端使離線掃描放慢，不增加無界記憶體 Queue；Ctrl+C 通知掃描工作停止並等待已開始的 I/O 收斂。底層檔案／終端 I/O 若卡住，仍無法安全強殺執行緒。

檢查與顯示在同一次掃描中完成，分段雜湊讀到 Footer 才能確認。畫面先明示尚未完成驗證，最終以摘要為準：無已知缺口退出 0，不完整／未知退出 2，操作失敗退出 1，中止退出 130 且不宣稱檢查完成。不修改來源、不要求 Token 或建立 Gateway 連線。

## 與線上 API 的界線

使用 V1 已定義的 recording_state 與 Snapshot 欄位，不更動 Wire Contract。關閉功能時仍為 disabled/not_recording。這不是 Audit、磁碟 Replay API、自動重連或常駐錄製的完整保留政策。實體 Win11 單 UART 錄製、雙 UART 與 30 分鐘接收仍需個別驗證。
