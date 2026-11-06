# Win11 COM3 一鍵只讀接收

## 目前已知與驗證範圍

使用者已確認 COM3 接著 IPCAM；只有 TX／RX／GND，DTR／RTS 未接，使用 115200／8N1／無流控。2026-11-05 使用者以原手動流程啟動 Gateway，並用 `uart.exe watch` 收到 IPCAM 開機訊息；這是既有 Win11 本機 RX 的實機證據。

新增的一鍵啟動器已在 Linux 以自動化替身、真子程序及 POSIX PTY（虛擬 Serial）測試；後續使用者已回報 Win11 Quickstart、Ctrl+C 後重啟、重複啟動及 MobaXterm 占用交叉測試全部通過。實機證據來自使用者執行回報，並非 Agent 直接操作 Windows。設計選擇與程序生命週期見 [啟動器設計審查](local-launcher.md)。

本次固定只讀：不 Confirm、不取得 TX Lease、不補換行、不發送 IPCAM 指令。DTR／RTS 沿用 false，不新增控制線操作；軟體設定不能證明 Driver 開啟瞬間沒有脈衝。

## 日常入口：互動式工作台（新功能）

更新並完成安裝後，可雙擊 `run-workbench.cmd`，或在專案根目錄的 PowerShell 執行：

```powershell
.\.venv\Scripts\uart.exe menu
```

選單整合本機裝置清單、單／雙通道監看、錄製、使用已存配置，以及錄製檔的檢查、回看與文字匯出。選 3 可選 UART 並錄製，預設存到 `recordings`；監控中 Ctrl+C 會清理本次 Gateway，再返回工作台。選 5 可手動選擇已有的錄製 Channel，不會自動播放最新檔案。

晚上先用既有錄製檔測選 5 的 I／V／T，再用單 UART 測選 3；Ctrl+C 後確認返回工作台，能再次啟動。雙 UART 在兩組設備齊備時另測。選單輸入處 Q／Enter 或 Ctrl+C 可離開，EOF 也會明示並退出。

需要其他錄製位置，可執行 `uart menu --recordings-dir 'D:\uart-recordings'`。所有文字匯出與 Profile 保存仍只建立新檔。若無法確認 Gateway 已清理，整個工作台會停止，不會繼續開啟端點。

此入口屬本機／離線操作；需要 d3s1 遠端 RX 時，使用後文原手動 Gateway、共用 Token 與 watch 流程。詳細選單、限制與驗收見 [工作台說明](workbench.md)。目前為 Linux／PTY 自動化驗證，Win11 雙擊、Ctrl+C 及實體 UART 工作台流程尚待實測。

## 遠端工作台：家裡同機與公司 dxan

`menu --remote` 連線既有 Gateway，不需手動輸入 Channel ID；公司仍須先維持共用 Token 與 SSH Tunnel。家裡可用同一台 Win11 的單 UART 測試，不必連 d3s1。

先關閉舊本機 Gateway 與 MobaXterm 的 COM3 連線，在專案根目錄的同一個 PowerShell 執行：

```powershell
git pull --ff-only origin work
$env:UART_GATEWAY_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$UartPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$UartGateway = Start-Process -FilePath $UartPython -ArgumentList '-m uart_gateway.server.app --serial --config examples/gateway.com3.toml' -WorkingDirectory (Get-Location).Path -PassThru
```

等待 Gateway 視窗顯示「Gateway 已接聽 127.0.0.1:18081」，再於原 PowerShell 執行：

```powershell
.\.venv\Scripts\uart.exe menu --remote
```

新 Gateway 視窗繼承這個 PowerShell 的 Token 環境變數，工作台使用同一份來源；Token 不放在命令列。這是手動 Gateway 流程，測完要到 Gateway 視窗按 Ctrl+C 關閉；退出遠端工作台不會停止它。如果 COM 不是 COM3，先在私人設定副本中修正 adapter.port，並將啟動指令的 --config 指向該副本。

在公司已建好 Gateway／SSH Tunnel 時，dxan 執行：

```sh
cd ~/uart-gateway-remote-test
git pull --ff-only origin work
.venv/bin/uart --token-file "$HOME/.config/uart-gateway/com3.token" menu --remote
```

驗收順序：

1. 核對 Channel 名稱、端點及狀態，輸入編號，再按 Enter 看即時資料。
2. Ctrl+C 後應返回清單，Gateway 視窗繼續執行；重新選相同 Channel，選 R 可看到仍保留的歷史，再接即時資料。
3. 選單中的 R 會重新查詢；正常清單 Q／Enter 離開工作台。失敗或全部通道未就緒的恢復選單中，Enter 僅保持等待，Q 才主動離開；選單提示時 Ctrl+C 退出整個工作台。
4. 若 Token 不符，應顯示 AUTH_FAILED；更新指定檔案或環境變數後，按 R 才重讀並連線，不會自動重試。
5. 若 Gateway 在選擇後重啟，或 Channel 的資料流／Binding 已改變，應要求重新選擇，不顯示另一個資料流。Tunnel 斷線則顯示原因並等待手動 R。

新增遠端選單已有 Linux Fake Serial＋真 Gateway／SDK／CLI／PTY 證據；使用者後續回報 Win11 同機一般流程（原第 4 項）通過。公司 d3s1 新選單與 USB 完整恢復仍待個別驗證，不以既有遠端 30 分鐘成功取代。

### 遠端模式拔除 USB 後的恢復

若看到同一 Gateway Instance、Channel 為「裝置遺失／DEVICE_MISSING」，而 `uart ports` 已列出 COM3，表示端點再次可見，但仍需要人工重開。R 只查詢；插回 USB 不自動開啟。新版 Gateway 視窗會獨立印出開啟中、就緒、裝置遺失、故障、關閉等日誌，包含通道、端點、修訂、Epoch 與錯誤碼；即使沒有 Client 或未錄製，也會顯示。不輸出 UART 原文或 Token。

原 `examples/gateway.com3.toml` 仍只給 read，不能重開。要測新功能，第一次更新時先在 Gateway 視窗按 Ctrl+C 正常停止舊程序，確認其他通道可以中斷，再明確改用 [RX 管理範例](../examples/gateway.com3.rx-reopen.toml)。此範例給 read＋configure，沒有 tx；configure 也允許既有 Binding 管理，請只交給有權管理該通道的人。工作台與 reopen 不會呼叫 Confirm。

在仍持有原 Token 的 PowerShell 啟動；不重新產生 Token，以免公司 Client 認證失敗：

```powershell
git pull --ff-only origin work
if (-not $env:UART_GATEWAY_TOKEN) { throw '此 PowerShell 沒有原 Token，請先恢復原本的受保護 Token 來源。' }
.\.venv\Scripts\uart.exe ports
$UartPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$UartGateway = Start-Process -FilePath $UartPython -ArgumentList '-m uart_gateway.server.app --serial --config examples/gateway.com3.rx-reopen.toml' -WorkingDirectory (Get-Location).Path -PassThru
.\.venv\Scripts\uart.exe menu --remote
```

先等新視窗顯示「已接聽」再操作工作台；若選單先顯示連線失敗，待 Gateway 就緒後按 R。使用 file: Token 的公司配置，請在自己的 `.local.toml` 副本中只調整該 Principal 的 permissions 為 `["read", "configure"]`，保留原 Token 參照、Channel、錄製與其他設定，啟動指令指向該副本；不要用單通道範例覆蓋原多通道配置。

本次新功能的實機測試步驟：

1. 選擇 COM3 的 Channel 並即時監看，拔除 USB；Gateway 應印出 lost／DEVICE_MISSING，工作台停止該訂閱，Enter 保持等待。
2. 插回同一個 USB-UART，按 R 更新，仍應為 lost；不會自動重開。
3. 選擇不可用的 Channel 編號，輸入 O，再輸入 Y。Gateway 日誌應顯示單通道重開、關閉、開啟中與 ready；工作台回到清單。此後不必重啟 Gateway。
4. 確認 Gateway Instance 不變、Binding 為 unconfirmed、新 Epoch，再選編號與 L／Enter，確認新的 IPCAM 輸出可見。記憶體歷史從新世代開始，舊錄製檔保留。
5. 負向驗證：尚未插回就重開，應顯示 DEVICE_MISSING；被 MobaXterm 占用時應顯示 PORT_BUSY 與關閉占用程式的提示。失敗後按 R 查詢，再人工重試，不會自動連續重開。
6. 使用只有 read 的原配置時，不應出現可重開選項；直接執行 reopen 應顯示 PERMISSION_DENIED。ready 通道不允許重開；舊 Gateway 缺少擴充能力時請先更新。

也可在同一 PowerShell 明確執行一次：

```powershell
.\.venv\Scripts\uart.exe reopen ch_uart_1
```

公司 dxan 的同一操作沿用受保護 Token 檔：

```sh
.venv/bin/uart --token-file "$HOME/.config/uart-gateway/com3.token" reopen ch_uart_1
```

若重開結果未確認或請求逾時，先 R／channels 查詢，不直接重送；Driver 尚未收斂會拒絕 CHANNEL_BUSY。重開核對原 Selector 及本程序前次成功開啟時已知身分；換成不同 Adapter 或 COM 變動時不猜測，需在 Gateway 電腦核對私人配置再正常啟動。只有 Port 且一直沒有身分資訊時仍為 unverified，操作者須自行核對實體接線。

舊版本或沒有管理權限時，可由管理者重開單路；整體 Gateway 重啟仍是人工備援，會中斷全部通道並清除記憶體歷史。工作台不代操 Gateway 程序。2026-11-07 使用者回報 Win11 同機 USB 手動恢復、未插回及 MobaXterm 占用／釋放後恢復通過；後續雙 IPCAM 測試亦回報 COM3 拔除與手動重開時 COM4 未曾中斷。公司 dxan 新操作仍待測；雙擊／Profile 與雙路錄製須另行驗證。

### 一個視窗同時監看 COM3／COM4（新功能）

本節是新的雙路合併畫面；先前使用兩個獨立 Client 視窗的實機隔離通過，不代表本入口亦已驗證。若已有雙通道 Gateway 且權限正確，可沿用原程序，更新 Client 後直接從下方 menu 命令開始。需要另行啟動時，先正常停止原 Gateway，保留原 Token，關閉 MobaXterm 的 COM 連線，再於專案根目錄使用以下範例：

```powershell
git pull --ff-only origin work
if (-not $env:UART_GATEWAY_TOKEN) { throw '目前 PowerShell 沒有原 Token，請先恢復原 Token 來源。' }
$UartPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$UartGateway = Start-Process -FilePath $UartPython -ArgumentList '-m uart_gateway.server.app --serial --config examples/gateway.dual.rx-reopen.toml' -WorkingDirectory (Get-Location).Path -PassThru
```

此範例明確使用 COM3／COM4、115200／8N1／無流控、DTR／RTS false，兩路均為 read＋configure，沒有 tx。也可沿用先前的私人 `dual-rx-test.local.toml`；公司既有多路／錄製／file: Token 配置請用原私人檔，不直接以範例取代。

等 Gateway 顯示兩路就緒與已接聽後，在同一個 PowerShell 執行：

```powershell
.\.venv\Scripts\uart.exe menu --remote
```

1. 核對清單，輸入 `1,2`，再按 Enter 即時接收。畫面應同時出現 `[ch_uart_1／ipcam_COM3]` 及 `[ch_uart_2／ipcam_COM4]` 的新資料；不必再開另一個 watch 視窗。
2. 拔掉 COM3 的 USB-UART，應只有第一路停止，第二路繼續顯示新資料。輸入 `R` 再按 Enter，核對第一路為裝置遺失。
3. 插回原 Adapter，輸入 `O 1` 並按 Enter，再輸入 `Y` 並按 Enter。確認等待期間 COM4 仍持續，成功後 COM3 在同一畫面重新接收，新 Epoch／未 Confirm；COM4 的 Epoch 不變。
4. 在重開確認時輸入 N 應取消；若尚未插回或被 MobaXterm 占用，應明示失敗且不自動重試。釋放占用後 R 更新，再 O 1／Y 重開。
5. `S 1`／`S 2` 只對已停止、快照 ready 的一路重新建立即時訂閱，不操作 Serial。需要時先 R；對正在接收的一路應拒絕重複訂閱。
6. 輸入 `Q` 或按 Ctrl+C，兩條 Client 都清理、回到外層清單，Gateway 繼續。再按 R 才重新查詢；外層 Q 離開。測完到 Gateway 視窗按 Ctrl+C 正常關閉。

工作區的 1／2 依本次選擇順序；若以 `2,1` 進入，O 1 即操作第二條原清單通道。R／Enter 不重開或自動重連。顏色各路獨立，合併畫面的 CR／退格採跳脫顯示；RX 會穿插提示與指令輸入，這是可互動的合併文字畫面，沒有固定分割窗格。只有 read 的 Token 可雙路監看，不能重開。

這輪為 Linux Fake Serial／真 TCP／SDK／CLI／PTY 與模擬 Windows 鍵盤驗證；Win11 新畫面與公司 dxan 操作仍待回報，公司驗證維持下週回公司後再測。完整行為見 [工作台說明](workbench.md)。

## 安裝與更新步驟

建議使用 Git 的 `work` 分支取得最新版，安裝後根目錄有固定 COM3 的 `run-com3.cmd` 與互動選擇的 `run-uart.cmd`。先前提供的 ZIP 屬當時快照，不包含之後所有功能；若要測本輪選單，請從 Git 更新。

```powershell
git status --short
git pull --ff-only origin work
```

若拉取失敗，保留本地變更與錯誤，不用 reset 強制覆蓋。下方 ZIP 解壓流程僅供使用先前測試包的情況；Git 工作目錄可直接進行安裝。

使用測試包時，可在 PowerShell 解壓至全新目錄；若目錄已存在，請換名稱，保留原有工作。

```powershell
$UartRoot = Join-Path $env:USERPROFILE 'uart-gateway-launcher-test'
$UartArchive = Join-Path $env:USERPROFILE 'Downloads\uart-gateway-launcher-test.zip'
Expand-Archive -LiteralPath $UartArchive -DestinationPath $UartRoot
Set-Location $UartRoot
```

在專案根目錄執行一次安裝；已有 `.venv` 時可略過建立環境，只重新安裝專案。

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[server]'
.\.venv\Scripts\uart-gatewayd.exe --list-ports
```

需要 Python 3.12 以上。若 `py -3` 選到舊版，先用 `py -0p` 查看，再指定已安裝的 3.12 以上版本。直接使用虛擬環境的執行檔，不必執行 Activate.ps1 或變更 PowerShell Execution Policy（執行原則）。安裝會取得 setuptools／pySerial；下載失敗時保留錯誤，勿停用 TLS 驗證。

`--list-ports` 只枚舉，不開啟 COM；核對目前仍為 COM3。啟動器不會擅自改選其他裝置。

## 列出與互動選擇 COM Port

在專案根目錄執行以下命令，只列出本機端點、裝置描述、序號、VID／PID 與位置，不需要 Token，也不會啟動 Gateway 或測試開啟端點：

```powershell
.\.venv\Scripts\uart.exe ports
```

需要選擇端點時，可雙擊 `run-uart.cmd`，或在 PowerShell 執行：

```powershell
.\.venv\Scripts\uart.exe local --select --read-only --watch
```

輸入清單編號才會啟動，只有一個裝置也不會自動選取。第一次選單的 `R` 重新整理，`Q`、直接 Enter 或 Ctrl+C 可取消；取消時不建立 Gateway。沒有 USB-UART 時仍可查看空清單、重新整理及取消，在家不需連到公司 d3s1。

在 `--select` 模式，USB 失去連線或訂閱終止後，程式會顯示原因、清理本次 Gateway，再回到重新枚舉的恢復選單。啟動或連線失敗也可重新選擇；不會自動沿用舊 COM。恢復選單中的 `R` 或 Enter 只重新整理，不會取消或啟動任何端點；輸入 `Q` 或按 Ctrl+C 才主動離開。插回 USB 後重新整理，再輸入目前的編號。每次重新啟動都是新的 Gateway／資料流與 Token，前次記憶體歷史不保留；這與 SSH Tunnel 重連到原 Gateway 不同。

若輸入來源結束（EOF），無法繼續等待鍵盤，會明確顯示「輸入來源已結束（EOF）」後退出；不要將它當成 Gateway 或 USB 清理失敗。請保留該訊息，並在互動式 PowerShell 重新執行。空白 Enter 與 EOF 現在有不同處理及訊息。

Ctrl+C 仍代表結束整個互動流程；到達 `--duration` 期限也會清理後退出，不回選單。固定 `--port`、`run-com3.cmd` 與遠端 watch 保持原本的退出行為。若無法確認本次 Gateway 已清理，會停止流程，不允許重新選擇後再啟動另一個程序。

清單以 COM 數字排序（例如 COM3 在 COM10 之前），裝置未提供的資訊會明示。輸入無效編號不會開啟端點。選擇後再次枚舉；端點消失、身分資訊變更或重複時會要求重新選擇，不把原編號套用到另一個裝置。啟動時也將可用身分條件交給既有 Gateway 載入器檢查。

枚舉不代表 COM 未被占用。實際開啟時若被 MobaXterm 占用，仍由原啟動器顯示錯誤並清理本次子程序。COM 名稱與序號不能證明轉接器後面的板子身分，這些檢查不取代人工核對接線。

`--select` 與 `--port` 不能同時使用；`uart local` 未指定兩者時仍預設 COM3。想直接指定端點或自動執行腳本，使用 `--port COM8` 等明確名稱。互動選單需要終端機輸入，不接受以管線送入編號。需要顏色與退格顯示時，可在互動式 ANSI 終端加上 `--terminal`。

使用者已在本輪居家測試回報 USB-UART／IPCAM 接收成功，`--terminal` 可正確顯示顏色。各情境證據以狀態文件為準，不一併視為全部通過。`--select` 預設仍選一個 Channel；兩條模式見下一節，沒有遠端枚舉或完整 TUI。

## 同時監看兩條 UART（新功能，Win11 實機待測）

先準備兩個 USB-UART，核對各自接線及 115200／8N1／無流控設定。若目前只有一個轉接器，可繼續使用原單通道功能，不將藍牙 COM 當成第二個 UART 測試來源。

在專案根目錄可雙擊 `run-dual-uart.cmd`，依序選擇兩個不同端點並命名。需要顏色並保存配置時，使用 PowerShell：

```powershell
.\.venv\Scripts\uart.exe local --select --count 2 --read-only --watch --terminal --save-profile .\bench.local.toml
```

例如將兩條分別命名為「IPCAM 主控台」與「MCU」。兩次選擇及命名全部完成後，才啟動**一個 Gateway**。命名時 Enter 使用預設名稱，Q 取消整組；重複端點或名稱會要求重選。兩條都 Ready 才進入監控；任一占用或啟動失敗會清理整組並回到選單。

同一視窗依來源顯示 `[ch_uart_1／IPCAM 主控台]`、`[ch_uart_2／MCU]`。每個顯示批次、每行都有標記，沒有換行的短片段也會定時顯示，可能被拆成數個帶標記的畫面行。`--terminal` 保留允許的顏色並隔離兩條的樣式；多通道畫面的 CR、退格顯示為 `\r`、`\x08`，避免改寫其他通道。原單通道 `--terminal` 的進度符號／退格行為不變。這是區分來源的合併監看，不是磁碟錄製。

保存的配置不含 Token。下次可直接執行：

```powershell
.\.venv\Scripts\uart.exe local --profile .\bench.local.toml --read-only --watch --terminal
```

啟動時會列出名稱與本次解析的 COM。已保存序號且其他條件仍唯一符合時，可跟隨 COM 重編號；不符合或模糊則要求重新選擇。換 USB 插孔可能改變位置條件，須重新選擇。沒有序號時保留原 COM 條件，不能保證同名 COM 一定是同一轉接器；仍需核對接線。`--profile`、`--select`、`--port` 互斥。

`--save-profile` 不覆蓋既有檔案；需要重新選擇及保存時請使用新檔名，例如 `bench-v2.local.toml`。後續回到選單的重新選擇不會改寫首次保存的檔案。配置格式見 [設定規格](configuration.md)。

監控時拔除其中一條，應顯示該條失去連線，另一條保持接收，Gateway 不重啟。拔回後此版本不自動恢復，需在適合中斷剩餘通道時按 Ctrl+C，再重開整組。兩條都停止後，`--select` 清理並返回選單；`--profile` 清理並退出。Ctrl+C 只結束本次 Gateway；正常 `--duration 30` 到期也清理退出。

請依序驗證：

1. 兩條都有資料時，核對名稱、來源與顏色，不互相混入。
2. 拔除第一條，確認第二條後續的新資料仍出現；不要只以舊畫面判定仍在接收。
3. Ctrl+C 後重新啟動，確認兩個 COM 都已釋放。再試第二次重複啟動，原監控應不受影響。
4. 讓 MobaXterm 占用其中一條後啟動，確認提示占用並清理本次整組，MobaXterm 仍正常。
5. 保存配置、關閉後重開；有序號且條件唯一的裝置重編 COM 時核對新的對應。條件改變時應拒絕，不自動選第一個裝置。

此 Launcher／Profile 流程已有 Linux／Mock 枚舉／兩個 PTY 自動化證據。Win11 手動雙通道 Gateway 的 COM3 故障／重開、COM4 持續接收已由使用者回報通過；不據此推論上述 CMD 雙擊、配置重用／重編 COM、整組占用清理或雙路長時間接收都已驗證。公司單路遠端 30 分鐘另有成功回報；五至六條 UART 與完整 TUI 仍待後續容量評估。

## 保存 RX 錄製（單 UART 可測，Win11 實機待驗證）

在 PowerShell 執行下列命令，選擇家裡的 USB-UART，即可一邊看 Log、一邊讓 Gateway 保存原始 RX：

```powershell
.\.venv\Scripts\uart.exe local --select --read-only --watch --terminal --record-dir .\recordings
```

要固定 COM3 時，將 `--select` 換成 `--port COM3`。要在 30 秒後正常關閉可加 `--duration 30`；按 Ctrl+C 亦會關閉本次 Gateway 並等待檔案收尾。啟動時會顯示錄製位置，每次執行都是新的 Capture，不覆寫舊檔。未指定 `--record-dir` 時不錄製；雙擊既有 CMD 的預設亦不錄製。

目錄例如 `recordings\capture-執行個體ID\ch_uart_1`，每條 Channel 分開保存。`recordings` 已被 Git 忽略。保存原始 bytes 與裝置／UART／Epoch 快照，不受 `--terminal` 顏色或文字顯示影響；UART 內容可能包含板子的帳密，分享前請自行確認內容。

停止錄製後可用以下 PowerShell 找出最新一組並檢查：

```powershell
$UartCapture = Get-ChildItem .\recordings -Directory -Filter 'capture-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
.\.venv\Scripts\uart.exe recording-info (Join-Path $UartCapture.FullName 'ch_uart_1')
```

此命令只讀錄製檔，不需要 Token、不開啟 UART。應顯示正常收尾、RX bytes 及「對已觀測資料無已知缺口」；退出碼 0 表示這些檢查通過，2 表示不完整或連續性未知，1 為參數／檔案等操作失敗。請在 Capture 已停止後檢查；錄製中尚未出現最終 Footer 屬預期。

檔案是 JSONL，`data_b64` 可還原任意 bytes，不能直接當成純文字 Log。格式、容量及判讀見 [RX 錄製規格](recording.md)。預設每段 64 MiB、本次每通道最多 1 GiB；超過上限停止該條錄製並告警，RX 監控繼續。這一版不自動刪除舊 Capture，請自行管理磁碟空間。

單 UART 驗收建議：

1. 啟動並讓 IPCAM 產生新 Log，按 Ctrl+C，執行 `recording-info` 核對 RX bytes 大於零且正常收尾。
2. 再錄製一輪，中途拔除 USB，確認畫面提示裝置遺失；關閉後檢查應顯示連續性未知，不能顯示無已知缺口。
3. 再次啟動應建立不同的 Capture，前次檔案仍保留。選單重新啟動時，記憶體歷史會重建，已保存的檔案保留。

目前已通過 Linux Fake Serial、真 Gateway／SDK 及 PTY 的自動化錄製測試；尚未操作 Win11 實體 UART 錄製。雙 UART 實機驗證依使用者指示延後到公司；30 分鐘持續接收仍待測。

## 將錄製轉成可讀文字

停止錄製後，在專案根目錄執行以下 PowerShell，找出最新 Capture 的第一條 Channel 並匯出：

```powershell
$UartCapture = Get-ChildItem .\recordings -Directory -Filter 'capture-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$UartChannel = Join-Path $UartCapture.FullName 'ch_uart_1'
.\.venv\Scripts\uart.exe recording-text $UartChannel --output .\recordings\uart-readable.txt
notepad .\recordings\uart-readable.txt
```

命令自動處理 `000001.jsonl`、`000002.jsonl` 等所有分段，UTF-8 中文跨區塊也能正確接續。每批有原始時間、Epoch 與區塊位移；CR／退格／顏色控制碼及非 UTF-8 bytes 會跳脫顯示。文字按 RX 區塊分批，可能分成多個帶標記的行。

輸出檔已存在時請換新檔名，例如 `uart-readable-2.txt`，不會覆蓋舊檔。若原錄製有缺口、損壞或未正常收尾，仍可看到可讀部分，但末尾會標示不完整／連續性未知，退出碼為 2；不能將此文字當成完整擷取。原始 JSONL 保留，文字格式與限制見 [錄製規格](recording.md)。

先前手動 PowerShell 將每塊單獨當 UTF-8 解碼，可能拆壞跨區塊中文字並額外插入換行，且只讀第一段；現在請使用正式命令匯出。

## 直接回看錄製的顏色與開機畫面

回家後不必接 UART 或連 d3s1。從 Git 更新程式，用現有錄製檔回看：

```powershell
git pull --ff-only origin work
$UartCapture = Get-ChildItem .\recordings -Directory -Filter 'capture-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$UartChannel = Join-Path $UartCapture.FullName 'ch_uart_1'
.\.venv\Scripts\uart.exe recording-play $UartChannel --terminal
```

會依序快速顯示所有分段，保留換行、退格進度符號與允許的顏色，正常分段間的 UTF-8／ANSI 亦會接續。這是離線內容，不按原始時間等待；畫面會明示離線回看及 Epoch／起始位移／時間。按 Ctrl+C 可中止並重設顏色，退出 130；中止時尚未完成整份錄製檔案檢查。

請先以一份已知正常的 IPCAM 錄製檢查顏色、換行及中文，完成時應有完整性摘要。使用曾拔除 USB 的錄製時，應看到當時裝置遺失及連續性未知，不得顯示無已知缺口。缺口、世代變更或格式異常會切斷舊解碼狀態；部分損壞仍可顯示可讀部分，最終退出碼為 2。

`--terminal` 需要互動式 ANSI 終端；重導向時會拒絕啟動。若只想安全地快速看文字，可移除 `--terminal`，控制字元會跳脫顯示；保存成可讀文字檔請用前節的 `recording-text`。檔案檢查須讀到 Footer 才能確定結果，請以最後摘要為準。自動化使用 Linux 虛擬終端；使用者後續已回報第 3 項離線操作通過，完整證據以 status.md 為準。

## 固定 COM3 的啟動與關閉

1. 關閉 MobaXterm 的 COM3 Serial 連線。若舊手動 Gateway 仍在執行，先在它的視窗按 Ctrl+C 正常停止；啟動器不會接管或結束它。
2. 在檔案總管雙擊專案根目錄的 `run-com3.cmd`。它會開啟獨立 Python 監控視窗。
3. 等待「COM3 已就緒；只讀 RX 監控中」。這表示 SDK 已完成認證，Channel 為 ready、端點正確、UART 為 115200／8N1／無流控且權限只有 read；不是等待固定秒數就宣稱成功。
4. IPCAM 有輸出時即可看到 UART Log。先顯示這次 Gateway 仍保留的歷史資料，再接即時 RX；模式會明示。無新資料不代表 Port 開啟失敗，且無法讀回先前被 MobaXterm 接走的內容。
5. 在監控視窗按 Ctrl+C。啟動器會關閉 SDK，通知本次 Gateway 正常釋放 Serial，等待子程序結束後退出。一般正常關閉後該視窗會結束；需要檢查清理訊息時，使用下方 PowerShell 方式。

每次啟動均產生新的臨時 Token，只保留於啟動器記憶體與其 Gateway 子程序環境；不需手動建立 Token，也不寫入原始碼、設定或命令列。臨時 TOML 只有秘密參照，結束時刪除。既有 PowerShell 的 Token 環境變數不會被修改。

同一使用者重複啟動同一端點會被程序鎖擋下；占用中的 COM 另由既有 Serial 獨占機制保護。錯誤視窗會提示原因並等待 Enter，讓你讀完後關閉。只會清理本次建立的 Gateway，不以程序名稱或 Port 反查並終止其他程式。

## 在既有 PowerShell 啟動

這個方式方便保留 Ctrl+C 後的輸出；與雙擊使用同一套 Python 啟動器。

```powershell
.\.venv\Scripts\uart.exe local --port COM3 --read-only --watch
```

可改指定 COM Port；此版本固定只讀並持續監控，`--read-only`／`--watch` 用來明示操作意圖。預設 Gateway 只綁定 `127.0.0.1:18081`。若其他端點的 Gateway 使用 18081，可為這次指定另一個 TCP Port；這不允許兩個 Gateway 同時開啟 COM3。

```powershell
.\.venv\Scripts\uart.exe local --port COM3 --read-only --watch --gateway-port 18082
```

`--startup-timeout 20` 控制就緒等待秒數，`--duration 30` 可在監控 30 秒後正常清理。從 PowerShell 手動執行不需 `--pause-on-error`；這是雙擊入口保留錯誤視窗的選項。

文字顯示使用增量 UTF-8，無效 bytes 與控制字元跳脫；CR 顯示成 `\r`，LF／TAB 保留，避免將 UART 的終端控制序列當作畫面命令。這不改動原始 RX bytes。此模式沒有持續磁碟錄製或零遺失保證。

需要換行、退格進度符號與 ANSI 顏色時，可在支援 ANSI 的互動式終端使用 `uart local --port COM3 --read-only --watch --terminal`。這是可選模式，雙擊入口仍維持原本的跳脫顯示；未知控制序列不轉送。遠端 CLI 使用 `watch ch_uart_1 --replay --terminal`，取代 `--text`。使用者已回報居家本機接收時可正確顯示顏色；Container 合併測試包的安裝與驗收見 [遠端 RX 測試](remote-rx-test.md)。

## 失敗如何處理

| 提示或現象 | 處理方式 |
| --- | --- |
| 找不到專案虛擬環境／套件 | 在此目錄完成首次安裝；雙擊入口不會自動下載套件 |
| 此端點已有本機啟動器執行中 | 使用原監控視窗，或在原視窗按 Ctrl+C 後重試；不刪鎖檔搶占 |
| 127.0.0.1:18081 已被使用 | 原 Gateway 不會被接管；自行正常停止它，或為不同 COM 指定 `--gateway-port` |
| COM3 無法獨占開啟／`PORT_BUSY` | 關閉 MobaXterm 或其他 Serial 連線後重試；部分 Windows 權限失敗也可能歸為此類，不代表能識別占用程序 |
| 找不到 COM3／`DEVICE_MISSING` | 用 `uart ports` 或 `--list-ports` 核對 USB-UART 與 COM 名稱，再重新選擇或明確指定端點 |
| 所選端點已消失、資訊改變或無法唯一識別 | 依重新顯示的清單再次選擇，核對轉接器；不沿用原編號自動開啟 |
| `LOCAL_CLEANUP_FAILED`／無法確認本次 Gateway 已清理 | 流程停止，不回選單；檢查訊息中本次建立的 PID，確認已結束後再啟動，不停止其他 Gateway |
| Gateway 提早結束 | 查看上方 Gateway 訊息，確認 `[server]` 已安裝與 Python 版本；不會進入假的就緒狀態 |
| 就緒逾時／`SERIAL_IO_FAILED` | 保留上方不含 Token 的訊息，檢查裝置、Driver 與 UART 設定；失敗時會清理本次子程序 |
| 顯示已就緒但沒有新 Log | 確認 IPCAM 是否正在輸出；Ready 只證明接收端點可用 |
| 拔除後 Channel lost／訂閱結束 | 選單模式清理後可手動重新選擇；固定端點模式仍退出，不自動重連同名 COM |

## 保留原手動模式與遠端界線

原 `uart-gatewayd --serial --config examples/gateway.com3.toml` 與 `uart watch` 仍可使用；手動 Gateway／Client 必須從受保護來源提供同一個 Token。範例只授予 read，Binding 為 unconfirmed 不妨礙 RX。`watch --duration 30 --replay --text` 可沿用文字監控；拿掉 `--text` 則輸出保留 bytes 的 JSON／Base64。

本機啟動器自行管理臨時認證，不提供另一個視窗或遠端 Client 取用該 Token 的功能。需要本機／遠端共用 Gateway 時，仍採原有明確憑證及 SDK／CLI 連線模式。使用者已確認 dxan 經 SSH Tunnel 的只讀接收，重播與新顯示模式見遠端測試文件。本機已增加兩條模式，Windows TUI 留待後續；現有載入器與 Gateway 上限仍為 4 個 Channel，未宣稱已支援 5～6 個 UART。

## Win11 實機驗收清單

- 完成安裝後雙擊，核對 COM3 就緒與 IPCAM 真實 RX；若需確認重播／即時，記錄當時 IPCAM 是否有輸出。
- 保持第一個視窗，再雙擊一次；第二次應提示已有啟動器，第一個仍可接收。
- 在第一個視窗按 Ctrl+C，再啟動一次；應可重新開啟 COM3。之後關閉啟動器，以 MobaXterm 確認 COM 已釋放。
- 讓 MobaXterm 占用 COM3 再雙擊；應顯示占用提示並清理失敗的子程序，MobaXterm 不受影響。
- 保持原手動 Gateway，再雙擊；應提示 18081 被使用，原服務仍正常。

回報 Windows／Python／Driver 版本及成功或失敗步驟，不附 Token。使用者已確認 Quickstart 與上述關閉重啟、重複啟動、MobaXterm 占用三項測試全部通過；這些範圍記為 Verified（使用者執行並回報）。既有手動 Gateway 同時存在的測試未另回報。

## 新選單實機驗收（接收與顏色已回報，其餘待測）

- 沒有裝置時執行 `uart ports`，確認顯示空清單；雙擊 `run-uart.cmd` 後可重新整理或取消。
- 插入 USB-UART，按 R 後核對描述、序號與 COM，確認 COM3／COM10 等數字排序正確。
- 輸入無效編號、按 Enter 或 Ctrl+C 取消，確認沒有 Gateway 占用 COM。
- 選擇裝置後確認 Ready、UART RX、Ctrl+C 清理及再次啟動；接收效果需有 IPCAM 或其他資料來源才能驗證。
- 選擇期間拔除／換接裝置，確認不會將舊編號自動套到另一個裝置；讓 MobaXterm 占用選定端點，確認提示與原連線保護。


## 居家實機補充

使用者已回報實體接收與終端顏色正常，IPCAM 電源重新上電時仍持續顯示；USB 拔插時程式中斷。拔除後不自動重連屬目前設計，但尚未取得退出訊息與重新插入後的操作結果，不能將所有插拔／清理情境一併標為通過。30 分鐘持續接收仍待之後執行。

## 新版手動恢復驗收（尚待 Win11 執行）

1. 以 `local --select --terminal` 選擇並接收 UART 資料。
2. 拔除 USB-UART，確認看到裝置失去連線的提示，以及本次 Gateway 清理後重新出現選單。若顯示其他錯誤，保留完整訊息，不一律當作 USB 遺失。
3. 在 USB 尚未插回時按 Enter，確認只重新整理並繼續等待，不退出也不開啟其他 COM。插回後按 R 或 Enter，核對現有 COM、描述及序號；重新輸入編號才開始監控。即使 COM 名稱相同，也不得自行啟動。
4. 確認新的資料來源有輸出時能再接收；前一 Gateway 的歷史不會重播。
5. 監控中按 Ctrl+C，確認整個程式結束；使用 `--duration 10` 時應在期限後清理並結束。

這一輪先維持單 Channel、只讀與手動選擇；不加入自動重連、TX 或完整 TUI。


使用者在 `34842cd` 更新後已回報 USB 拔除後可停留於恢復選單，基本恢復選單操作成功。這不代表長時間接收、所有 EOF 條件或多 UART 同時操作已驗證。
