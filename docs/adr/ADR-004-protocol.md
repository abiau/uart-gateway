# ADR-004：Structured Protocol 與相容性

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

系統需要 Channel（通道）、Session（工作階段）、Principal（認證身分）、RX Subscription（訂閱）、Gap（缺口）、Lease（租約）及 TX 狀態。Raw TCP（原始串流）本身不能表達這些管理語意，UART 又可能包含任意 Binary Data（二進位資料）。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| Raw TCP 為唯一介面 | 簡單，部分終端相容 | 缺少管理語意與標準授權／錯誤格式 |
| JSON Lines + Base64 | 易診斷、跨語言、保留 bytes | Base64 與 Metadata（中繼資料）有額外成本 |
| Binary Protocol（二進位協定） | 空間效率較高 | 解析、除錯與版本成本較大 |
| WebSocket／HTTP | Browser（瀏覽器）整合方便 | 目前沒有對應需求，增加部署面 |

## 最終建議與選擇原因

以 JSON Lines 結構化主介面、UTF-8 Frame（訊框）與 Base64 UART Payload（負載）建立 V1。候選採分開 Hello（版本握手）及 Authentication（認證），再提供 Request／Response／Event（請求／回覆／事件）。Protocol 模組獨立，CLI（命令列）與 TUI（文字介面）共用 SDK（開發套件）。

115200／8N1 的理論單方向原始速率約 11520 bytes/s；目前更應優先保證錯誤與容量語意，不為未量測的效能問題先設計二進位格式。

## 技術影響

Framing（訊框分界）使用 LF，Frame 大小與 Base64 解碼量有上限。版本採 Major／Minor（主／次版本）與 Capability（能力）；所有線上格式以 [V1 規格](../protocol/v1.md) 為唯一來源。

Raw 若有需求，使用不同 Listener（接聽端），預設關閉且先只讀，不在同 Port（連接埠）自動猜測協定。Telnet、Raw TCP 與 RFC2217 不相同，MobaXterm 模式需實測。Raw 不得繞過同一 Core 的資源管理。

候選以 U64 十進位字串避免跨語言整數精度問題；RX `seq` 定義為 Chunk（區塊）序號，Offset（位移）負責完整性。這些是本次新增細節，不冒充原審查已接受的規格。

## 未來變更條件

若量測顯示編碼成本成為瓶頸或確有 Web 需求，可提出額外 Transport（傳輸方式）或新協定版本，但保持 SDK 公開操作與 Core 邊界。破壞性變更不能以新增可選欄位名義偷偷改變語意。

## 相關規格與待核准事項

[Protocol V1](../protocol/v1.md)、[設定](../configuration.md) 與 [部署](../deployment.md)。Q-06／Q-07 相容需求及 Q-19 完整欄位、序號、握手、Port／Limits（上限）均待 [核准](../status.md)。
