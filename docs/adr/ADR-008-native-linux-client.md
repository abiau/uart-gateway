# ADR-008：Linux 獨立 Client 採 Go

- 狀態：Accepted
- 日期：2026-11-13
- 授權依據：使用者討論 Linux Client 改寫、保留相同協定後，明確要求「現在請開始實作並測試」。

## 問題與決策

Ubuntu 14.04／16.04 現場不宜為 UART 工具額外安裝 Python 3.12。將 Linux Client 編為 Go 的 Linux amd64 靜態執行檔，停用 cgo、採 GOAMD64=v1，只用標準函式庫。Windows Python Gateway／Serial／Recorder、Protocol V1 與既有 Python SDK／TUI 保留，沒有服務拆分。

CLI、文字工作台與 Shell 流程共用 go-client/client。認證、角色、Expected Context、Confirm／Lease／Fencing、收據先保存、未知不重送與人工 Shell 核對均維持原原則。固定連線與憑證格式相容；Go 收據及 Shell 核對另存，避免跨實作誤判。Windows 部署預設送原生 ELF，Python ZIP 只保留明確相容模式。

## 替代方案與影響

Rust 可做相同工作，但舊 Linux 發行須管理 musl／工具鏈組合；C／C++ 需自行維護更多 JSON、TCP、Unicode 及生命週期處理。Go 在本輪以標準函式庫完成網路與跨平台建置，產物較大但仍在受限部署大小內。這不保證所有老舊 CPU／核心都相容，實際架構與核心仍需驗證；官方 Linux 最低核心依 [Go 文件](https://go.dev/wiki/MinimumRequirements) 核對。

兩種 SDK 存在協定驗證的重複，須以共用 V1 規格及 Go 對真 Python Gateway 的測試維護相容。原 Textual／離線錄製工具尚未移植，不把文字工作台宣稱為完整 TUI 替代。此決策增補 ADR-001 的部署方式，不取代單一 Windows Gateway、Core 邊界或既有安全決策。
