# ADR-001：部署模型與模組邊界

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11～Q-14、Q-17 的現場資訊仍待取得。Phase 1 尚未開始。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

Windows 11 是實體 COM Port 的唯一擁有者，Linux d3s1 的 Podman Container（容器）執行 Client（用戶端）。系統需支援多 Channel（邏輯通道）、多 Reader（讀取者）與未來工具，但沒有需要分散式部署硬體控制的證據。

原提案的 `server/gateway/session/core` 若都成為獨立層或服務，容易增加轉呼叫、狀態同步與故障處理成本。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| 單一平面程式 | 起步快、部署容易 | TCP、Serial 與 UI（使用者介面）易耦合，不利測試 |
| 單程序、內部模組化 | 明確邊界且不增加跨程序協調 | 仍需防止阻塞 I/O 與共享狀態失控 |
| 每通道程序／微服務 | 可隔離部分故障或獨立擴展 | IPC（程序間通訊）、生命週期、授權及部署更複雜 |

## 最終建議與選擇原因

採單一 Windows Gateway Process（程序），`server.app` 組裝 Core（核心領域邏輯）、Serial Backend（裝置實作）、Protocol（通訊協定）與 Recorder（錄製器）。合併 `server/` 與 `gateway/` 的責任；Session（工作階段）先保留為 Server 內模組。

Client SDK（用戶端開發套件）作為 CLI（命令列）、TUI（文字介面）及未來 Agent（代理工具）的共同邊界。先用單一 Python Distribution（發布套件）與選用依賴，不提前拆成多個發布單位。

原因是目前維護與故障語意比跨主機擴展重要；模組邊界足以支援測試與後續演進。

## 技術影響

Core 不依賴 Server、Client、pySerial 或 TUI；Protocol 不依賴任一端。Serial／磁碟阻塞工作不能在 asyncio Event Loop（事件迴圈）直接執行。每 Channel 獨立狀態與有界資源；單程序不代表全域單一 Buffer（緩衝）或 TX Lock（鎖）。

網路路徑採 Loopback（回送介面）與 SSH Forward（轉送）；Container 的第二段 Local Forward（本地轉送）是候選部署設計，尚未驗證。

## 未來變更條件

若量測顯示特定 Driver 故障必須程序隔離、單程序資源上限不足，或真的需要多台硬體 Host（主機），再提出新 ADR。不得僅為架構形式改成微服務。

## 相關規格與待核准事項

[系統架構](../architecture.md)、[部署](../deployment.md)、[開發規範](../development.md) 是具體描述。Q-01／Q-02 容量、Q-13／Q-14 網路與 Q-18 Package 政策見 [核准清單](../status.md)。本決策未授權實作或部署。
