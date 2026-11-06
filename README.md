# UART Gateway

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

UART Gateway 是面向嵌入式韌體開發的 UART 通訊工具。Windows 11 獨占 USB-UART 對應的 COM Port，Linux d3s1 上的 Podman Container（容器）透過 SSH Tunnel 使用共同 Client SDK（用戶端開發套件），供 CLI（命令列介面）、未來 TUI（文字使用者介面）及 Agent（代理工具）操作。

日常操作可雙擊 `run-workbench.cmd` 或執行 `uart menu`，從文字工作台選擇本機監看、錄製、既有配置，以及錄製檔的檢查／回看／文字匯出。監控中 Ctrl+C 清理後返回工作台，選單輸入處 Q／Enter 離開。步驟見 [工作台說明](docs/workbench.md)；Win11 工作台實機流程尚待驗證。

遠端選單使用 `uart --token-file <受保護的Token檔案> menu --remote`，連線既有 Gateway 後以編號選 Channel，再選即時或重播；Ctrl+C 返回清單，Gateway 繼續執行。居家同機與公司 dxan 的具體步驟見 [Windows Quickstart](docs/windows-quickstart.md)。

遠端 USB 拔插後，COM 重新出現不會自動重開原 Channel；須在 Gateway 電腦以相同 Token 手動重啟，再按 R 查詢。恢復選單 Enter 保持等待，Q 離開。使用者已回報 Win11 更新、本機監看／錄製、離線操作與遠端同機一般流程通過；雙 UART 及遠端 USB 手動恢復仍待測，詳見專案狀態。

## 目標與設計方向

- 單一 Windows Gateway Process（程序），內部採模組化。
- 支援獨立 Channel（邏輯通道），避免把 Adapter（轉接器）、COM 名稱與 Project（專案）永久綁死。
- RX 可供多個 Client 訂閱，具有有界緩衝、Replay（重播）及 Gap（資料缺口）回報。
- TX 使用 Exclusive Lease（獨占租約）與過期操作隔離；結果不明時不自動重送。
- 主協定採 TCP、JSON Lines 與 Base64，保留任意 UART bytes。
- 不直接對公司網路暴露 Gateway；SSH Tunnel 之外仍有應用層認證與授權。

目前已提供 **Fake Serial、pySerial Backend、Gateway、SDK、CLI 與本機一鍵啟動器**。安裝後可雙擊根目錄的 `run-com3.cmd`，自動等待 COM3 就緒並監控 RX；Ctrl+C 只清理本次 Gateway。也可執行 `uart local --port COM3 --read-only --watch`，不需手動提供 Token。[Win11 COM3 接收步驟](docs/windows-quickstart.md) 包含安裝、錯誤排除與實機驗收清單。

本機端點可用 `uart ports` 只讀列出；雙擊 `run-uart.cmd` 或執行 `uart local --select --read-only --watch` 可互動選擇，再沿用既有只讀啟動流程。支援重新整理與取消，不會預設開啟第一個裝置。中斷後的恢復選單可按 Enter 重新整理，輸入 Q 或按 Ctrl+C 離開；輸入結束會另行明示 EOF。這一輪已完成 Mock／PTY 測試；使用者亦已回報居家 USB-UART／IPCAM 接收成功、`--terminal` 顏色正確。使用者另回報 USB 拔插會中斷監控、IPCAM 重新上電仍可顯示；本輪新增中斷清理後回到選單、由使用者重新選擇的流程，完整 Windows 插拔恢復仍待驗證。

使用者後續已確認 USB 拔除後能返回並停留於恢復選單。最新切片新增 `uart local --select --count 2 --read-only --watch --terminal` 與 `run-dual-uart.cmd`，以**一個 Gateway** 同時接收兩條 UART，畫面標明 Channel／名稱；一路遺失時另一路繼續。可用 `--save-profile bench.local.toml` 保存名稱與裝置條件，下次以 `--profile bench.local.toml` 重新核對後啟動。多通道畫面顏色獨立，CR／退格採跳脫顯示；完整步驟見 [Windows Quickstart](docs/windows-quickstart.md)。目前為 Mock／雙 PTY 自動化 Tested，Win11 雙實體 UART 尚未驗證。

Linux 虛擬端點已測試啟動器完整程序流程；使用者先前的 Win11 手動 watch 已讀到 IPCAM 開機訊息。使用者亦已回報新 Quickstart、關閉後重啟、重複啟動及 MobaXterm 占用交叉測試通過。

## 部署簡介

候選正式路徑為：Container SDK → Container SSH Local Forward（本地轉送）→ d3s1 SSH Reverse Forward（反向轉送）→ Windows Loopback（回送介面）Gateway → COM Port。

第一個實體驗證目標為 COM3，預設 115200 baud、8N1；COM3 是測試端點，不是永久裝置身分。Windows、d3s1 Host（主機）與 Container 的 `127.0.0.1` 位於不同網路命名空間，不能直接互換。

## 目前階段

Phase 0 已核准；Phase 1 已完成無硬體通路、Serial Backend 與 Win11 COM3 本機 RX 驗證。使用者亦已確認 dxan 遠端 RX、歷史重播、終端顏色／換行與再次監控成功；SSH Tunnel 中斷時 CLI 顯示斷線、Gateway 保持執行，重建 Tunnel 後手動重連與重播成功。居家接續已新增本機端點列出與互動選擇，使用者已回報實體接收與終端顏色成功；可先在家驗證較長時間的本機接收，公司遠端已由使用者回報 IPCAM 每秒輸出 ps 資訊、dxan 持續接收 30 分鐘正常。實體 TX、插拔、資料完整性量測與完整 Phase 1 驗收尚未完成。

七份 ADR（架構決策紀錄）均為 `Accepted`（已正式接受）。數值預設、具體協定契約及建議操作政策已接受為初始基線；尚未取得的現場資訊不視為已驗證。最新證據與待回答事項以 [目前狀態](docs/status.md) 為準。

## 文件索引

| 文件 | 用途 |
| --- | --- |
| [協作規則](AGENTS.md) | 開始工作前必讀；授權、依賴、文件與 Git 規則 |
| [系統架構](docs/architecture.md) | 模組、資料模型、生命週期與 Mermaid 圖 |
| [部署規格](docs/deployment.md) | Windows、SSH、d3s1、Podman 與證據邊界 |
| [設定規格](docs/configuration.md) | 設定參數、候選預設、合法範圍及安全性 |
| [開發規範](docs/development.md) | 未來 Python 結構、開發與測試方式 |
| [啟動器設計審查](docs/local-launcher.md) | CMD／PowerShell／Python 比較、就緒判定與子程序清理 |
| [Win11 一鍵接收](docs/windows-quickstart.md) | 安裝、雙擊、CLI、疑難排解與實機驗收 |
| [Container 合併測試](docs/remote-rx-test.md) | 單一測試包、遠端重播、終端顯示與驗收 |
| [開發路線圖](docs/roadmap.md) | Phase 0–4 範圍、前置條件與驗收 |
| [目前狀態及核准清單](docs/status.md) | 實際進度、差異、文件檢查與待決事項 |
| [協定 V1](docs/protocol/v1.md) | 唯一線上格式規範草案，含完整欄位與範例 |
| [ADR-001 部署模型](docs/adr/ADR-001-deployment-model.md) | 單程序與模組化 |
| [ADR-002 通道綁定](docs/adr/ADR-002-channel-binding.md) | 身分、換板與世代 |
| [ADR-003 Serial I/O](docs/adr/ADR-003-serial-io.md) | 非阻塞協調與工作路徑 |
| [ADR-004 協定](docs/adr/ADR-004-protocol.md) | JSON Lines、Base64 與版本 |
| [ADR-005 TX 操作權](docs/adr/ADR-005-tx-ownership.md) | Lease、Fencing 與不確定結果 |
| [ADR-006 RX 緩衝](docs/adr/ADR-006-rx-buffering.md) | 有界保留、Replay、Gap 與錄製 |
| [ADR-007 安全](docs/adr/ADR-007-security.md) | Loopback、SSH、認證與權限 |

## 第一批的安裝與模擬操作

需要 Python 3.12 以上。SDK／Fake Serial 沒有執行期第三方依賴，建置使用 setuptools；實體 Serial 安裝 `.[server]` 加入 pySerial。

Linux：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export UART_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uart-gatewayd --fake --config examples/gateway.fake.toml
```

另開終端機，從受保護的管道提供相同的 `UART_GATEWAY_TOKEN`，啟用同一虛擬環境後操作。不要將真實 Token 貼入文件、版本庫或共享紀錄。

```sh
uart channels
uart confirm ch_lab_1 --label fake_uart
uart send ch_lab_1 hello --newline lf
uart status ch_lab_1 TX_ID_FROM_SEND
```

最後一行的 `TX_ID_FROM_SEND` 請替換成 send 輸出的 `tx_id`。`send` 不自動補換行；此例明確指定 LF。輸出依序為 pending 與最終 TX 狀態；`written_to_driver` 不代表實體板子已執行命令。

可在第三個終端機先執行 `uart watch ch_lab_1 --duration 10` 再送出文字，觀察帶 `data_b64` 的 RX Event。Fake Serial 的 TX 會回送到 RX；這是模擬行為，不是實體 UART。CLI 的 stdout 為 JSON Lines，診斷放 stderr，保留二進位內容並避免直接解讀終端控制字元。

Win11 PowerShell 可使用下列安裝與模擬啟動方式；本次只在 Linux 驗證，尚未在 Win11 實測：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:UART_GATEWAY_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
uart-gatewayd --fake --config examples/gateway.fake.toml
```

## 測試與目前限制

錄製亦可直接用 `uart recording-play <Channel目錄> --terminal` 離線快速回看，呈現允許的顏色、換行及退格；Ctrl+C 可中止。命令不需要 UART 或 Token，會明示資料缺口與最後完整性結果。回家可使用既有錄製檔，步驟見 [Windows Quickstart](docs/windows-quickstart.md)。

錄製後可用 `uart recording-text <Channel目錄> --output uart-readable.txt` 匯出所有分段並以記事本查看。命令處理跨區塊 UTF-8，保留時間／Epoch／位移，跳脫控制字元，並在末尾標明完整性；詳細 PowerShell 操作見 [Windows Quickstart](docs/windows-quickstart.md)。

可用 `uart local --select --read-only --watch --terminal --record-dir ./recordings` 啟用 Gateway RX 錄製，單 UART 即可使用。各通道保存原始 bytes 與裝置／Epoch 快照，分段及佇列有界；錄製故障會告警，RX 繼續。停止後以 `uart recording-info 錄製的Channel目錄` 檢查正常收尾、位移與雜湊。操作見 [Windows Quickstart](docs/windows-quickstart.md)，格式及限制見 [錄製規格](docs/recording.md)。雙 UART 實機依使用者指示延後到公司，公司遠端持續接收 30 分鐘已回報通過；錄製完整性與其他長時間情境仍需個別驗證。

```sh
python -m unittest discover -s tests -v
```

第一批 Linux／Python 3.12 執行 41 項測試全數通過，包含真 TCP 與模擬裝置；已安裝的兩個命令入口亦完成端到端操作。測試需要建立 `127.0.0.1` 暫時 Socket；禁止 Socket 的沙箱需取得對應執行權限，不能將略過整合測試當成通過。

前次 RX 錄製驗證為 Linux／Python 3.12／pySerial 3.5，182 項測試全部通過、無略過。原 163 項包含單／雙通道、重播、占用、清理與配置；新增 19 項錄製單元／整合驗證二進位、輪替、容量、慢磁碟／溢位／錯誤、來源遺失、無 Client、活躍連線關閉、離線完整性及真 CLI／PTY SIGINT 收尾。這不是 Win11 硬體證據；未安裝 `[server]` 或非 POSIX 環境時，PTY 測試會明確略過。

目前載入器支援網路位址／Port、Channel／Principal、Adapter Selector、Serial 設定、自動 RX 開啟、生命週期等待期限與基本 RX 錄製；其他尚未支援欄位明確拒絕。錄製的跨 Capture 保留管理、稽核、完整設定與 Win11 錄製尚未驗證；SSH／Podman 的只讀 RX 與手動斷線恢復已由使用者實際執行確認。SDK 由呼叫者明確重連與恢復 Cursor，不自動恢復 Lease 或重送 TX。`watch --text` 可顯示文字並跳脫控制字元，預設仍輸出保留原始 bytes 的 JSON／Base64。互動啟動器可在清理後手動重選端點，並非自動恢復原工作階段。


## 遠端重播與終端顯示

`watch --text` 保留控制字元跳脫顯示；改用 `--terminal` 可在互動式 ANSI 終端呈現換行、退格與顏色，其他控制序列會被過濾。兩種模式都重用 SDK 與有界批次輸出，不新增 UART TX。既有 Windows Gateway 不需重啟，Container 的安裝與測試步驟見 [合併測試](docs/remote-rx-test.md)。

最新文字匯出切片：完整 190 項自動化測試通過，包含原 182 項與 8 項文字匯出回歸；使用者已回報 Win11 匯出後可看到文字，此測項通過。詳細驗證邊界見專案狀態文件。

離線回看切片：完整 200 項自動化測試通過；使用者後續已回報第 3 項離線操作通過。

本機工作台切片：完整 215 項自動化測試通過；使用者後續已回報第 1～3 項一般流程通過，CMD 雙擊與完整 USB 恢復未另推論。

遠端工作台切片：完整 234 項自動化測試通過；Win11 同機一般流程已由使用者回報第 4 項通過，dxan 新選單與 USB 完整恢復仍待測。

最新恢復提示改善：完整 239 項自動化測試通過；恢復選單 Enter 保持等待與同 Token 手動重啟後恢復已有模擬證據，實體重測待回報。
