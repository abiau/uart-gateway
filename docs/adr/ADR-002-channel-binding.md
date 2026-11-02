# ADR-002：Channel、裝置身分與 Binding

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11～Q-14、Q-17 的現場資訊仍待取得。Phase 1 尚未開始。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

Adapter（轉接器）不永久對應 Project（專案）或 Board（板子）。COM 名稱會改變或被重用；同 Adapter 只移動 UART 線時可能沒有 USB 事件。沿用舊 Label（顯示名稱）或 Lease（租約）可能操作錯誤目標。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| COM 名稱作永久身分 | 簡單、容易輸入 | 無法辨識重編號或同名裝置 |
| Adapter 序號直接等於板子 | 裝置匹配較穩定 | 序號可能缺失／重複，也無法辨識換板 |
| 穩定 Channel + 可變 Binding | 清楚表達邏輯通道、裝置與當次目標 | 需要明確的確認流程與世代檢查 |

## 最終建議與選擇原因

採穩定 `channel_id`，獨立 Binding（綁定）保存 Adapter 條件、Label、目標備註與 `binding_revision`。Session（工作階段）代表一次認證連線，Principal（身分）由憑證決定，兩者不等於 Channel 或 Board。

候選操作採 `binding.rebind` 停止舊端點與資格、人工換接、`binding.confirm` 授予新目標資格。每次成功確認產生新 Stream Epoch（資料流世代）；Server Restart（重啟）後候選允許先 RX，但 TX 需重新確認。

原因是軟體只能可靠管理明確的操作邊界，無法憑 Adapter 身分證明實際板子沒有更換。

## 技術影響

TX 必須帶 Expected Context（預期脈絡），檢查 Instance（執行個體）、Revision、Epoch 與 Fencing Token（隔離序號）。Binding 改變撤銷舊授權、終止舊訂閱並取消待送工作。Capture（擷取紀錄）保存當時快照，不受日後改名影響。

候選 Revision 在同 Instance／Channel 內遞增；重啟可重設數字，但新 Instance／Epoch 防止混用。V1 不提供獨立 Rename API；改名不應被誤用成換板確認。已進 Driver／UART Buffer（緩衝）的 bytes 不保證可撤回，使用者需在停止後換線。

## 未來變更條件

若有可驗證的板子序號、專屬控制介面或硬體插拔訊號，可新增輔助識別；仍不能無條件取消手動確認或讓不可信 Boot Log（開機紀錄）自動授權 TX。若改變 Restart 政策，需說明目標證據與失效方式。

## 相關規格與待核准事項

正式欄位候選見 [Protocol](../protocol/v1.md)，生命週期見 [架構](../architecture.md)。Q-04／Q-05 的詳細操作與 Restart 政策、Q-11／Q-12 的硬體資訊仍待 [核准](../status.md)。
