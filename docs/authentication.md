# SDK 自動認證與一次性初始化

## 簡化遠端設定與 Client 入口（2026-11-12）

Windows 在專案安裝完成後雙擊 `run-remote.cmd`，第一次選 **2**。新部署先選一至兩條 UART 與名稱，再輸入 SSH 目的地、Container 名稱（`-` 表示 Linux 主機）、角色及必要的連線資料。預設讀取；M 另安裝管理，T 才建立／安裝獨立 TX。已有固定部署會沿用原憑證，不能藉此為缺少的角色自動提權。安裝摘要按 Enter 執行，Q 取消；OpenSSH 正常核對主機指紋及登入，不關閉驗證，不保存 SSH 密碼。

選 2 只設定 Linux 目的地及安裝原角色，可在既有 Gateway 執行期間進行，不連線或檢查本機 Gateway TCP Port，也不修改既有 UART 設定。選 1 啟動新 Gateway 才會以短暫綁定檢查本機 Port，不發起 TCP 連線；占用／系統保留／綁定權限失敗會顯示原因並返回遠端選單，不停止其他程序。實際啟動仍由原 Gateway 再次取得接聽端，避免前置檢查後的競爭。

工具只要求 Linux 已有 Python 3.12 以上及可用 SSH；容器另需既有 Podman 與 host 網路。Client 與所選角色經同一 SSH 標準輸入傳輸，不需手動匯出／貼上／傳送 Token，也不需要 pip、虛擬環境或管理員權限。安裝後 Linux 日常執行：

```sh
uart
# 明確使用內建文字工作台
uart client --text
# 已安裝管理憑證才可選用
uart --access manage
```

已有 Textual／pyte 使用多窗格；未安裝則直接使用內建文字介面，不在啟動時下載依賴。固定連線／角色與原 CLI、SDK 共用。多窗格最多四路、文字同時最多兩路；每次都查詢目前通道，顯示本次目標，人工選定後才監看。依部署 ID 與角色記住上次通道 ID，不保存 UART 資料、秘密或 TX 確認資格。文字介面以 Enter 使用目前清單中仍就緒的上次選擇；多窗格僅預先勾選，仍須 Enter。消失、失效或改名的通道不會用舊快照啟動；同 ID 的新目標以新查詢結果顯示，TX 仍需重新核對。

之後 Windows 選 **1**（或 `uart gateway`）核對摘要並開始。D 可重新選一至兩條設備，依位置沿用邏輯通道 ID，保留固定 Token／部署 ID，保存前一份設定供檢查；新增通道明確加入 read／manage 範圍，既有 TX 範圍保留。移除仍在 TX 授權內的通道會拒絕，需先明確調整授權。一般重新啟動不改寫 UART 設定。這個固定遠端入口與原本機 S／local 的臨時憑證入口分開。

啟動時先開本次單一 Gateway，再透過 SSH 安裝／核對 Client 與原角色，建立 Loopback 反向轉送，最後從 Linux SDK 認證並查詢通道。只有此查詢成功才顯示「遠端已就緒」；不代表每條 UART 都就緒或硬體資料完整。R 人工恢復 SSH，斷線期間 Gateway RX／錄製繼續；Q／Ctrl+C 清理本次 Gateway 與 SSH，不接管既有程序。Linux 退出仍只關閉 Client。

若容器重建而遺失 Client，Windows 下一次啟動可從原部署重新安裝同一份憑證；不要求重新產生 Token。持久來源仍在 Windows，這不是自動替容器建立掛載；已有持久 HOME／XDG_CONFIG_HOME 可以繼續沿用。Container 使用者與權限變更時需重新核對安裝目的地。

Linux 入口在 `~/.local/bin/uart`，若目前 PATH 未包含 `~/.local/bin`，工具會提示直接執行 `~/.local/bin/uart`；不修改 Shell 啟動檔。已有其他程式／pip 建立的同名入口會拒絕覆蓋，須先自行確認。Python 與既有 SSH 登入仍是前提；使用現有 SSH 金鑰可避免重複輸入 SSH 密碼。可用 `uart setup` 重跑設定，目的地與權限預先帶入上次值。

本輪自動化涵蓋 Windows 引導邊界，以及 Linux 安裝、固定認證、真 TCP／SDK、互動入口與模擬 SSH 子程序；尚未驗證公司 SSH／Podman 與實體 UART 新流程。以下逐步初始化／匯出／匯入仍供進階部署使用。

## 日常使用

TX 入口為 `uart tx`／工作台 X，需明確建立 tx 角色；原 read／manage 不變。[TX 工作台](tx-workbench.md) 包含新部署、通道範圍與實機驗收。以下未加 --enable-tx 的初始化仍只產生兩個角色。

Container／Codex 的 `shell-ready`／`exec`／`exec-status` 同樣由 SDK 載入固定 tx 憑證，不需每天管理 Token；先安全匯入該角色並由人核對已登入的 Shell。它不自動提升原 read／manage 權限，步驟見 [Shell 操作](shell-exec.md)。

本機 `local`／工作台 S 沿用每次啟動的臨時 Token，不需設定。遠端使用固定受保護憑證，初始化及安裝一次後，CLI、遠端 Workbench、Python／Codex 工具都由 SDK 自動載入。Win11 ACL 與本機模擬 TX 的驗證見 [專案狀態](status.md)。公司 SSH 路徑仍待實測。

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

預設資料在 `%LOCALAPPDATA%\uart-gateway\connections\default`。`gateway.toml` 只有受保護秘密的檔案參照；`read.secret`、`manage.secret` 由工具產生。Windows 直接呼叫 Win32 API，在寫入內容前核對擁有者並設定僅允許目前使用者的 DACL，SDK 每次讀取時再核對擁有者及 DACL。一般使用者可操作自己擁有的私有檔案，檢查失敗會回報錯誤。

在 Gateway 視窗啟動：

```powershell
$UartGatewayConfig = Join-Path $env:LOCALAPPDATA 'uart-gateway\connections\default\gateway.toml'
.\.venv\Scripts\uart-gatewayd.exe --serial --config $UartGatewayConfig
```

再開另一個 PowerShell 執行前節 `menu --remote`。同一 Windows 使用者已具備這兩種憑證，不需再匯出／匯入。關閉 Gateway 請在其視窗 Ctrl+C；退出遠端工作台仍不停止 Gateway。

這是多身分的獨立遠端 Gateway 設定，不交給僅接受單身分的本機 `start --config`／S 啟動入口。一般本機臨時 Gateway 不因此變成遠端常駐服務。重複 `auth init` 會拒絕覆寫，避免每天啟動產生新 Token 而讓已配置 Client 失效。

## 透過 SSH 安裝到 dxan，一次即可

先讓 Win11 與 Container 都使用本版程式；公司 SSH／Podman 路徑沿用既有部署。下列沿用已知 `victor.hu-vo@d3s1`、容器 `dxan` 及容器內 `~/uart-gateway-remote-test`，路徑不同時需修正。此節的公司 SSH 路徑待實測。

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
| 明確 TX 工作台 | read＋configure＋tx | auth init --enable-tx 才建立；--tx-channels 限制通道，仍須人工核對、Confirm、Lease 與 Expected Context |

同一 OS 帳號若能讀取管理秘密檔，便能明確選管理模式；SDK 預設讀取不是 OS 隔離。要讓 Codex 僅有讀取能力，應只在其帳號／Container 安裝讀取包。共用一份讀取憑證也不提供不同工具的個別稽核身分。

固定來源失效、檔案遺失、權限過寬或 AUTH_FAILED 時，不會改讀環境變數、生成另一把遠端 Token 或提升權限。先確認 Client 來源對應正在執行的 Gateway，工作台按 R 才重新讀取及連線。Server 啟動時載入秘密，輪替需協調更新與重啟；只改 Client 檔案不能讓舊 Server 接受新 Token，本輪沒有自動輪替或遠端憑證 reload。

原 `--token-file`／`--token-env` 與 `Client.connect(host, port, token)` 保留相容。明確來源不與連線／角色參數混用；完全沒有固定設定時才相容既有 UART_GATEWAY_TOKEN。已存在的固定設定優先，損壞時也不降級。詳細路徑、格式、界限及優先序以 [設定規格](configuration.md#sdk-遠端自動認證) 為準。
