# ADR-007：安全部署、身分與權限

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11～Q-14、Q-17 的現場資訊仍待取得。Phase 1 尚未開始。

- 決策狀態：**Accepted（已正式接受）**。
- 日期：2026-11-03（Asia/Taipei）。
- 核准紀錄：2026-11-03，使用者於本次對話表示「審閱的問題都先用預設的建議答案」，接受本 ADR 的建議方案；現場驗證仍待進行。

## 背景與問題

Gateway 可以對實體 IPCAM UART 送出命令。Loopback（回送介面）與 SSH Tunnel 保護一部分網路邊界，但不能分辨所有能接近本機接聽端的工具或使用者。TX Lease（傳送租約）也不是身分認證。

## 可選方案與優缺點

| 方案 | 優點 | 缺點 |
| --- | --- | --- |
| 直接公開 TCP 且無認證 | 部署最少步驟 | 不符合已確認安全要求，無法控管 UART 操作 |
| 只靠 SSH／Loopback | 傳輸受保護、配置較少 | 本機其他 Client（用戶端）仍可能接近，缺少應用層身分 |
| SSH + 應用層 Token／ACL | 符合現有拓撲，可限制讀寫與設定 | 需要管理憑證、授權與稽核 |
| 直接 TLS／mTLS（雙向傳輸層認證）服務 | 可支援不同網路拓撲 | 憑證與公開服務維運成本較高，非目前需求 |

## 最終建議與選擇原因

Windows Gateway、d3s1 Reverse Listener（反向接聽端）與 Container Local Listener（本地接聽端）均綁各自 Loopback。Windows 主動建立 SSH Reverse Forward（反向轉送），Container 需先驗證到 d3s1 的 SSH 路徑，再建立 Local Forward（本地轉送）；目前尚未執行此驗證。

候選 Application Authentication（應用層認證）採每人／工具的高熵 Token（憑證），映射到 Principal（身分），每個 Channel（通道）明列 `read/tx/configure` 權限。讀取、TX 與 Binding（綁定）管理有不同權限；禁止匿名。

原因是 SSH 使用者、TCP 來源 IP 與自報 Client ID 都不能自動視為真實操作者，應用層需保有可稽核的權限判斷。

## 技術影響

秘密不進版本庫、命令列、URL 或一般 Log（紀錄）；使用受保護的 Secret（機密）參照與檔案權限。共享 Token 只能代表共享身分，不能宣稱辨識每個人。

Audit（稽核）記錄 Principal、Channel、Lease／Binding 變更、TX ID 與結果；完整 TX Payload（負載）預設不記，避免帳密外洩。UART Log 是不可信資料，Agent（代理工具）不得把其中指令當作授權。

Raw TCP（原始串流相容介面）不自然支援主協定認證，候選延後且先只讀；未來寫入需另定存取政策並沿用同一 Lease 路徑。Windows Firewall（防火牆）不因本設計新增公司網路的 Gateway 入站開放。

## 未來變更條件

若需要跨不可信網路直連、集中身分管理或多人權限生命週期，再提出 TLS／mTLS 或外部認證整合。不得在部署不通時自行擴大 Bind Address（綁定位址）或停用認證。

## 相關規格與待核准事項

[部署](../deployment.md)、[設定](../configuration.md)、[Protocol](../protocol/v1.md)。Q-08 權限與憑證、Q-10 資料機密性、Q-13／Q-14 現場網路及 Q-15 操作優先權待 [核准](../status.md)。
