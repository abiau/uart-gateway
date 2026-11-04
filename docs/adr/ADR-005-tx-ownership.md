# ADR-005：TX Ownership 與不確定結果

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

多個 Client（用戶端）逐次呼叫 Serial write 即使受 Mutex（互斥鎖）保護，仍可能在整段 Shell 操作中交錯。Client 斷線後也不能永遠占有操作權。TCP Timeout（逾時）無法證明命令尚未送出。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| 單次 Write Lock | 防止同一次呼叫交錯 | 無法保護一段互動操作，也缺少身分與到期 |
| 永久 Owner（持有者） | 操作權清楚 | 異常退出容易留下占用 |
| Lease + Fencing + 單一 Writer | 有界持有、阻止舊 Owner 並序列化 I/O | 需定義 Renew（更新）、到期與派送競爭 |

## 最終建議與選擇原因

每 Channel（通道）最多一個 Lease，綁定 Principal（身分）、Session（工作階段）及 Binding／Epoch（綁定／資料流世代）。TX 入列及 Dispatch（真正派送）前都檢查 Expected Context（預期脈絡）與 Token。

候選 V1 不自動搶占；到期或釋放取消尚未開始的工作，等待已開始 I/O 收斂。使用 Server Monotonic Clock（單調時鐘），候選 TTL（有效期間）15 秒、5 秒更新，但數值未核准。

原因是持有者互斥與物理傳輸結果是兩個不同問題，不能只用 Lock 或 TCP Response（回覆）混為一談。

## 技術影響

TX 狀態為 `pending`（已接受）、`rejected`（尚未開始 I/O 即拒絕）、`written_to_driver`（Driver 接受全部 bytes）、`unknown`（結果不明）。Driver 成功不代表板子已執行命令。任何未知寫入都不得自動以相同或新的 TX ID 補送。

候選採有界結果快取，接受前預留紀錄槽，保留期內不提前淘汰；容量滿即拒絕。相同 Principal／Instance（執行個體）可於重連後查詢；Server Restart（重啟）或紀錄不存在時只能回 unknown。有限去重不等於跨故障恰好一次。

Unknown 後候選封鎖該 Channel 新 TX 並要求人工復原。已在 Driver／UART Buffer（緩衝）中的 bytes 無法保證撤回，Lease 也不能保證 Shell 已回到乾淨提示字元。

## 未來變更條件

若需要管理者強制接管、Owner 優先序或特定設備的可去重命令協定，需新增明確政策與測試。不能把裝置專屬確認機制推廣成任意 UART 的 exactly-once 保證。

## 相關規格與待核准事項

契約見 [Protocol](../protocol/v1.md)，數值見 [設定](../configuration.md)。Q-04／Q-05、Q-15 交接及 Q-19 狀態／快取細節仍待 [核准](../status.md)。
