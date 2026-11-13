# Linux 獨立 Client

Go Client 將原本依賴 Linux Python 的日常操作編成單一 Linux x86_64 執行檔。Windows Gateway、固定角色與 Protocol V1 沿用；Linux 不需要安裝 Python、Go、pip 或虛擬環境。現場證據以 [目前狀態](status.md) 為準，CPU／核心與安裝條件見 [部署](deployment.md)，參數及檔案界限見 [設定](configuration.md#go-client-本機資料與界限2026-11-13)。

## 第一次及日常使用

在已安裝專案的 Windows 雙擊 `run-uart.cmd` 選 R，或直接雙擊 `run-remote.cmd`，選 **2**，輸入 Linux SSH 主機。直接使用主機時，Container 選 `-`。一般接收選 R；已有明確管理／TX 需求才選 M／T。安裝沿用原固定憑證，不會因新 UART 或重新安裝而重生 Token。

設定完成後 Windows 選 **1** 啟動 Gateway／Tunnel；Linux 執行：

```sh
uart
# PATH 尚未包含入口時
~/.local/bin/uart
```

輸入通道編號，例如 `1` 或 `1,2`，核對本次清單後開始歷史＋即時監看。Ctrl+C 返回後立即查詢目前通道，Enter 可沿用本次清單中仍就緒的上次選擇，不必再先按 R。R 可重新查詢失敗的連線與目標；Q 離開 Client。更新清單不自動開始 RX 或重開 UART，開始前仍使用新快照核對，退出不停止 Windows Gateway。

## 腳本及 Agent

```sh
uart channels
uart watch ch_uart_1 --duration 5 --replay
uart watch ch_uart_1 --text
uart --access manage reopen ch_uart_1
```

預設 watch 輸出 Protocol 事件 JSON，RX 的 data_b64 保留精確 bytes。文字模式適合閱讀，無法取代原始 bytes；原生終端模式須在 TTY 明確使用 --terminal。多路文字依來源加標記，CR／退格跳脫，不提供 Python 多窗格的獨立終端模擬。資料缺口、Epoch／目標變更或慢輸出超限會明示停止，不能把已顯示內容當作完整錄製。

多路 watch 的單路失效不停止健康通道；結束時任一路失敗會回退出碼 2，方便腳本辨識部分接收，不因另一通道仍有資料而回報全部成功。

管理操作需要人工核對。單次傳送會先預覽精確 bytes 與換行，再由人輸入 Y：

```sh
uart send ch_uart_1 'help' --newline cr
uart status ch_uart_1 <TX_ID>
```

已由人確認目標登入 POSIX sh／BusyBox ash 且換行正確後：

```sh
uart shell-ready ch_uart_1 --newline cr
uart exec ch_uart_1 'uname -a' --timeout 10
uart exec-status <execution_id>
```

shell-ready 只保存本次目標核對，必要時人工 Confirm，不傳 UART bytes。exec 沿用 TX 角色、Expected Context、整段 Lease、完成標記／exit code 與有界輸出；結果不明只用 exec-status 查原操作，不重送、不自動登入或送 Ctrl+C。不能由 Driver 接受推論 IPCAM 已執行。詳細語意仍見 [Shell 操作](shell-exec.md)。

send／status：0 為已知完整 Driver 接受，1 為拒絕或操作錯誤，2 為 pending／unknown；不是板子執行狀態。exec：0 為觀察到完成且 exit code=0，1 為命令非零或操作錯誤，2 為未確認完成。輸出截斷會另外標示，不能只看退出碼判斷輸出完整。

## 與 Python 入口的關係

| 功能 | 原生 Go | 原 Python |
| --- | --- | --- |
| 固定連線、read／manage／tx、Protocol V1 | 共用格式與 Gateway | 保留 |
| 接收、重播、多路文字、人工 RX 重開 | 提供 | 保留 |
| Confirm／Rebind、TX 預覽與查詢、Shell 命令 | 共用 Go SDK | 保留 Python SDK |
| Textual 多窗格／TX 表單、離線錄製檢查／搜尋／匯出 | 尚未移植 | 使用原入口 |
| Windows COM／Gateway／錄製 | 不操作 | 原程式負責 |

Go `tui` 是文字工作台別名，並非 Textual 介面。Go 與 Python 的 Shell 核對／TX 收據目錄分開；切換實作後需重新人工 shell-ready，不能拿另一實作的舊收據自動取得新操作資格。固定憑證不變，Python 原套件仍可用明確路徑執行。

## 建置與交付

Windows 開發端準備 Go 1.24 以上工具鏈後，在專案根目錄：

```powershell
.\build-go-client.ps1
# 工具鏈不在 PATH 時
.\build-go-client.ps1 -GoExe 'C:\path\to\go.exe'
```

產物為 `dist/uart-linux-amd64` 及 `.build.json`，記錄編譯器與 SHA-256。以 CGO_ENABLED=0、GOOS=linux、GOARCH=amd64、GOAMD64=v1 建置，沒有第三方 Go 模組；發行須附執行檔，Python wheel 不包含它。dist／開發工具鏈不加入 Git，日常啟動不下載編譯器。

Go 模組責任與相容策略見 [ADR-008](adr/ADR-008-native-linux-client.md)。新增命令也必須經共用 Go SDK；不可繞過認證、租約或直接連 Serial。
