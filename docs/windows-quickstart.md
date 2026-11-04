# Win11 COM3 首輪接收驗證

## 目前已知與驗證範圍

使用者已確認 COM3 接著 IPCAM；只有 TX／RX／GND，DTR／RTS 未接，使用 115200／8N1／無流控。2026-11-05 使用者實際執行本版本庫後，回報 Gateway 可啟動且 uart.exe watch 能讀到 IPCAM 開機訊息，因此 Win11 本機 RX 已驗證。此處保留重現流程；Agent 未遠端操作 Win11，實體 TX／插拔與 SSH 路徑仍待驗證。

本次先在 Win11 本機執行 Gateway 與 CLI，確認 Serial RX；尚不配置 SSH／Podman。COM3 範例只授予 `read` 權限，無法 Confirm、取得 Lease 或 TX，不會補送換行或 IPCAM 指令。DTR／RTS 設為 false；控制線沒有接到板子，但仍不宣稱 Driver 開啟瞬間沒有脈衝。

## 取得程式與安裝

可使用 origin/work 的完整程式，或先前提供的 `uart-gateway-win11-test.zip` 測試快照。此次交接已將程式與測試納入 Git 保存；若使用 Git 工作目錄，直接進入目錄安裝即可，不需要再解壓。測試包不含 Linux 虛擬環境、Git 資料或真實憑證。

下載到預設 Downloads 後，開啟 PowerShell，在新的測試目錄解壓。若目錄已存在，請換一個新名稱，不要覆蓋其他工作。

```powershell
$UartRoot = Join-Path $env:USERPROFILE 'uart-gateway-win11-test'
$UartArchive = Join-Path $env:USERPROFILE 'Downloads\uart-gateway-win11-test.zip'
Expand-Archive -LiteralPath $UartArchive -DestinationPath $UartRoot
Set-Location $UartRoot
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[server]'
.\.venv\Scripts\uart-gatewayd.exe --list-ports
```

需要 Python 3.12 以上。若 `py -3` 選到舊版本，先以 `py -0p` 查看，再指定已安裝的 3.12 以上版本。此處直接使用虛擬環境的執行檔，不需要變更 PowerShell Execution Policy（執行原則）或執行 Activate.ps1。

安裝會取得 setuptools／pySerial；若公司網路阻擋套件下載，保留錯誤訊息另行處理，不停用 TLS 驗證。`--list-ports` 只枚舉，不開啟 COM；應可找到 `port` 為 `COM3` 的紀錄。缺少序號不會自動選其他裝置，實際開啟仍固定本次指定的 COM3。

## 啟動只讀 Gateway

保持同一個 PowerShell 視窗，建立本次臨時 Token。啟動的新視窗會繼承環境變數，Token 不放進命令列、設定檔或輸出。

```powershell
$env:UART_GATEWAY_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$UartGateway = Start-Process -FilePath (Resolve-Path '.\.venv\Scripts\python.exe').Path -ArgumentList @('-m','uart_gateway.server.app','--serial','--config','examples/gateway.com3.toml') -WorkingDirectory (Get-Location).Path -PassThru
```

等待新視窗顯示 `Gateway 已接聽 127.0.0.1:18081`。這只代表 TCP 已啟動，不保證 COM 已成功開啟；下一步必須看 Channel 狀態。Gateway 初始只有 RX，Binding 為 unconfirmed。

## 在原 PowerShell 視窗查看資料

```powershell
.\.venv\Scripts\uart.exe channels | ConvertFrom-Json | ConvertTo-Json -Depth 8
.\.venv\Scripts\uart.exe watch ch_uart_1 --duration 30 --replay --text
```

成功條件：

- Channel 為 `ch_uart_1`、`state` 為 `ready`、`adapter.port` 為 `COM3`，UART 設定為 115200／8N1／無流控。
- `binding.status` 為 `unconfirmed` 是預期行為，接收不需要 Confirm。
- IPCAM 產生新資料時，watch 顯示文字。若 IPCAM 已開機且目前安靜，30 秒沒有新 Log 不能單獨判定故障；Gateway 不能取回 MobaXterm 先前已讀取的資料。
- `--replay` 先顯示此 Epoch 最早仍保留的資料，再接即時接收；stderr 會標示模式。這不保證從開機起完整保存，也不能回復 Ring 已淘汰的紀錄。
- `--text` 使用增量 UTF-8 顯示，無效 bytes 與控制字元跳脫；CR 會顯示成 `\r`，LF／TAB 保留。拿掉 `--text` 可保存完整 JSON Lines／Base64，兩者沒有改動原始 RX bytes。

若需保留這次接收紀錄，可另執行下列命令；這是 CLI 事件輸出，不是尚待完成的 Gateway 常駐 Recorder。

```powershell
.\.venv\Scripts\uart.exe watch ch_uart_1 --duration 30 | Out-File -Encoding utf8 -FilePath uart-com3-rx.local.jsonl
```

不要重開 MobaXterm 同時操作 COM3。測試完成後，在 Gateway 新視窗按 Ctrl+C 正常關閉。原 PowerShell 關閉後，這次 Token 不再保留；若更換視窗，需明確提供相同 Token 才能連線。

## 失敗如何解讀

| 現象 | 最小檢查與處理 |
| --- | --- |
| `PORT_BUSY` | 確認其他 Serial Terminal 已退出；Windows 的占用與部分權限失敗可能都回同一類錯誤，不能單靠代碼判定哪個程序占用 |
| `DEVICE_MISSING` | 重跑只枚舉的 `--list-ports`，核對 COM 是否改名、USB 是否仍連接 |
| `SERIAL_IO_FAILED` | 保留 Channel 的 last_error 及 Gateway 訊息，核對 Driver／設定；不自動重送命令 |
| `AUTH_FAILED` | 確認 CLI 仍在啟動 Gateway 的原 PowerShell，沒有重新產生不同 Token |
| TCP 無法連線 | 檢查 Gateway 新視窗與 18081 是否已被占用；不改成對外綁定 |
| `ready` 但看不到新資料 | 確認 IPCAM 是否正在輸出；無新 bytes 不等於 Port 開啟失敗 |
| 拔除後 Channel 為 lost | 這是預期保護；不自動換接同名 COM。首輪只讀設定可正常停止 Gateway，核對端點後重新啟動 |

使用者已回報本機 watch 接收成功，進度記為 Verified（使用者執行並回報）。後續若需診斷，可補充 list-ports、channels 與相關 Log，但勿附上 Token。下一步是 Linux／SSH Tunnel 整合，本機 RX 成功不代表遠端路徑或實體 TX 已驗證。
