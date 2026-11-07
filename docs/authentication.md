# SDK 自動認證與一次性初始化

## 日常使用

本機 `local`／工作台 S 沿用每次啟動的臨時 Token，不需設定。遠端使用固定受保護憑證：初始化及安裝一次後，CLI、遠端 Workbench、Python／Codex 工具都由 SDK 自動載入。這是已實作的新流程；自動化採 Linux／真 TCP／Fake Serial／PTY，Win11 ACL、實體 UART 與公司 SSH 路徑仍待實測。

初始化後，Win11 可使用：

```powershell
.\.venv\Scripts\uart.exe menu --remote
.\.venv\Scripts\uart.exe --access manage menu --remote
```

Container 對應為：

```sh
.venv/bin/uart menu --remote
.venv/bin/uart --access manage menu --remote
```

第一條預設讀取，第二條明確選管理；管理憑證需另行安裝。日常不必產生環境變數、輸入 Token 或指定 Token 檔案路徑。Gateway 與既有 SSH Tunnel 仍須先啟動；此功能不建立服務或 SSH Tunnel。

## Win11 Gateway 首次初始化

在專案根目錄，先依實際設備確認完整來源設定。單 COM3 範例：

```powershell
.\.venv\Scripts\uart.exe auth init --gateway-config .\examples\gateway.com3.rx-reopen.toml
```

兩路使用 `examples\gateway.dual.rx-reopen.toml`，COM 不同時先修改私人設定副本。初始化只驗證設定並建立新副本，不開啟 Serial、不啟動 Gateway、不取得 Lease 或傳送指令；來源設定不改寫。新的副本授權為讀取及管理兩個獨立身分，不產生 tx 權限。

預設資料在 `%LOCALAPPDATA%\uart-gateway\connections\default`。`gateway.toml` 只有受保護秘密的檔案參照；`read.secret`、`manage.secret` 由工具產生。Windows 在寫入內容前設定只允許目前使用者的 ACL，再於 SDK 讀取時檢查；系統 PowerShell 不可用或 ACL 不符時失敗，不降低保護。

在 Gateway 視窗啟動：

```powershell
$UartGatewayConfig = Join-Path $env:LOCALAPPDATA 'uart-gateway\connections\default\gateway.toml'
.\.venv\Scripts\uart-gatewayd.exe --serial --config $UartGatewayConfig
```

再開另一個 PowerShell 執行前節 `menu --remote`。同一 Windows 使用者已具備這兩種憑證，不需再匯出／匯入。關閉 Gateway 請在其視窗 Ctrl+C；退出遠端工作台仍不停止 Gateway。

這是多身分的獨立遠端 Gateway 設定，不交給僅接受單身分的本機 `start --config`／S 啟動入口。一般本機臨時 Gateway 不因此變成遠端常駐服務。重複 `auth init` 會拒絕覆寫，避免每天啟動產生新 Token 而讓已配置 Client 失效。

## 透過 SSH 安裝到 dxan，一次即可

先讓 Win11 與 Container 都使用本版程式；公司 SSH／Podman 路徑沿用既有部署。下列沿用已知 `victor.hu-vo@d3s1`、容器 `dxan` 及容器內 `~/uart-gateway-remote-test`，路徑不同時需修正。Codex 未執行公司 SSH 或 Windows ACL，此節是待實測教學。

Win11 先匯出讀取憑證到使用者私有目錄：

```powershell
$UartReaderBundle = Join-Path $env:LOCALAPPDATA 'uart-gateway\transfers\d3s1-read.uart-credential'
.\.venv\Scripts\uart.exe auth export --access read --output $UartReaderBundle
```

透過 SSH 標準輸入直接安裝，不在遠端建立暫存 Token 檔案：

```powershell
Get-Content -Raw -LiteralPath $UartReaderBundle |
    ssh victor.hu-vo@d3s1 'podman exec -i dxan sh -lc ''exec "$HOME/uart-gateway-remote-test/.venv/bin/uart" auth import --stdin --name company'''
```

`podman exec -i` 保留標準輸入，不配置 TTY。資料只進入匯入程式，不顯示在命令列或終端。憑證包本身是秘密資料，僅透過現有受信任 SSH 傳輸，不貼到聊天／Log；不得關閉 SSH Host Key 驗證。匯出包已由 Git 忽略，完成安裝後可移除本機傳輸包，保留原角色秘密檔。

Container 首次安裝的連線會成為預設；若已有其他預設，可執行一次：

```sh
.venv/bin/uart auth use company
```

其後維持原 Tunnel，直接 `uart menu --remote`。Tunnel 在 Container 的本機 Port 若不同，匯入時加 `--port 其他Port`，或日常以全域 `--port` 明確覆寫；來源 Gateway 的 Port 不因此改變。

需要管理時，在 Win11 另行 `auth export --access manage --output 新的管理包路徑`，以同樣 SSH 流程匯入到 `company`。只匯入讀取包的環境不會自動取得管理來源；不同部署 ID、相異既有憑證或 Port 衝突會拒絕合併，要求使用其他連線名稱。相同內容可重複匯入，不會重置。

也可用 `auth import --file 路徑` 安裝已安全傳送的包；POSIX 檔案須屬目前使用者、限擁有者讀寫，Windows 須符合目前使用者 ACL。標準輸入模式只接受管線，不要求人在終端貼上秘密。

## CLI、Workbench 與 Codex 共用 SDK

```python
import asyncio
from uart_gateway.client import Client

async def main():
    async with await Client.connect_auto() as client:
        print(await client.list_channels())

asyncio.run(main())
```

具名連線使用 `Client.connect_auto("company")`，管理須明確指定 `access="manage"`。CLI 對應 `uart --connection company channels`；未指定名稱用已選預設連線，未指定角色用讀取。

SDK `resolve_connection()` 可建立固定設定，再多次 `await settings.connect()`；每次新 Session 都重新讀取所選憑證，但不在既有工作區中悄悄切換位址／預設連線。CLI／Workbench 使用同一入口，Codex 呼叫 Python SDK 或既有 CLI 即可，不需要模型處理 Token。此輪沒有新增 Codex MCP Server 或其他工具服務。

SDK 匯入不讀取憑證、不連線。檔案存取放在工作執行緒；連線仍走原 `hello`／`auth.authenticate`。Server 回覆的權限須符合所選模式，超出或管理模式沒有 configure 都會關閉連線，不把錯配憑證當成讀取。

## 權限與故障處理

| 模式 | Server 權限 | 用途 |
| --- | --- | --- |
| 預設讀取 | read | 列出／監看通道、歷史重播；不管理裝置 |
| 明確管理 | read＋configure | 原人工 RX 重開與 Binding 管理；不是 TX 授權 |
| 原有明確 TX 流程 | 另需 tx | 仍須 Confirm、Lease、Expected Context；本輪初始化不產生 tx 憑證 |

同一 OS 帳號若能讀取管理秘密檔，便能明確選管理模式；SDK 預設讀取不是 OS 隔離。要讓 Codex 僅有讀取能力，應只在其帳號／Container 安裝讀取包。共用一份讀取憑證也不提供不同工具的個別稽核身分。

固定來源失效、檔案遺失、權限過寬或 AUTH_FAILED 時，不會改讀環境變數、生成另一把遠端 Token 或提升權限。先確認 Client 來源對應正在執行的 Gateway，工作台按 R 才重新讀取及連線。Server 啟動時載入秘密，輪替需協調更新與重啟；只改 Client 檔案不能讓舊 Server 接受新 Token，本輪沒有自動輪替或遠端憑證 reload。

原 `--token-file`／`--token-env` 與 `Client.connect(host, port, token)` 保留相容。明確來源不與連線／角色參數混用；完全沒有固定設定時才相容既有 UART_GATEWAY_TOKEN。已存在的固定設定優先，損壞時也不降級。詳細路徑、格式、界限及優先序以 [設定規格](configuration.md#sdk-遠端自動認證) 為準。
