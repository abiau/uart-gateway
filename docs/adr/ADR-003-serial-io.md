# ADR-003：Serial I/O 執行模型

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11～Q-14、Q-17 的現場資訊仍待取得。Phase 1 尚未開始。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

pySerial 的讀寫與裝置開關可能阻塞。UART 高流量、Write Timeout（寫入逾時）、拔除或 Driver 卡住時，不能讓所有 TCP Client（用戶端）停止服務。多個 Channel（通道）也不能共用一個阻塞工作路徑。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| 在 Event Loop 直接呼叫 | 程式表面簡單 | 一次阻塞可能拖住所有連線與期限判斷 |
| 每 Channel 單一輪詢 Worker（工作路徑） | 容易序列化 Handle（控制代碼）操作 | 長寫入可能延誤 RX，需精細切片 |
| 每 Channel RX Reader 與單一 TX Worker | 讀寫互不因一般等待直接阻塞，符合常見 Serial 使用方式 | 必須協調 Close／Reconfigure（重設設定）與跨執行緒狀態 |
| 原生非同步 Backend（裝置實作） | 可能減少執行緒 | Windows 支援與依賴需額外驗證，不能先假定成熟 |

## 最終建議與選擇原因

候選採 asyncio 處理 TCP、Session（工作階段）、計時與協調，每個開啟 Channel 使用 RX Reader 執行緒及單一 TX Worker。所有工作經有界 Queue（佇列）交接，Core（領域邏輯）只依賴必要 Serial／Clock（時鐘）介面。

此方案避免網路處理被 pySerial 阻塞，也讓 TX 序列化與 RX 持續讀取有清楚邊界。實際執行緒與 Driver 行為仍需 Windows 驗證。

## 技術影響

Open、Close、Configure 與 Dispatch（派送）必須由 Channel 協調。Lease（租約）撤銷與真正開始寫入共用同步邊界；Worker 不得在舊 Token（隔離序號）失效後才開始工作。

Reader、Event Loop、Connection（連線）、TX 與 Recorder（錄製器）的 Queue 都有上限。正常空讀取不當作斷線；Partial Write（部分寫入）或逾時不當作未執行。無法收斂的 I/O 使該 Channel 停止新 TX，不能啟動第二個 Writer 繼續。

設定有限 Timeout 不保證所有 Driver 都會準時返回；Python 也不能安全強殺任意執行緒。實作需對卡住狀態提供診斷與明確程序復原途徑。

## 未來變更條件

若實測通道數、吞吐或 Driver 隔離需求超出此模型，再評估非同步 Backend 或程序隔離。不得在沒有量測前引入多套並行執行模型。

## 相關規格與待核准事項

見 [架構](../architecture.md)、[設定](../configuration.md)、[測試規劃](../development.md)。Q-01／Q-03 容量與速率、Q-11 控制線及 Q-19 有界等待細節仍待 [核准](../status.md)。
