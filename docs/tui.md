# 多窗格 UART 工作台

每條 UART 有獨立資料區與固定狀態列，底部固定操作按鈕與訊息，不用在接收文字中輸入指令。使用 Windows Terminal／PowerShell 或 Linux 互動終端，建議至少 100×30；兩路在較窄視窗改成上下排列，三至四路使用四宮格。

## 開始使用

在專案根目錄安裝可選依賴後，雙擊 `run-tui.cmd`，或執行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[server,tui]"
.\.venv\Scripts\uart.exe tui --local
```

啟動畫面沿用工作台的上次本機設定，未保存時使用 COM3。改端點請點 **選擇 UART**，以空白鍵勾選一至兩條，再點 **使用所選裝置**；這一步只枚舉、不開啟 Serial。可點 **切換錄製**，核對裝置與錄製摘要後按 Enter／**開始監看**。等待原 Launcher 完成認證與全部 Channel Ready 後，自動顯示歷史與即時資料。

預設只讀；使用已授權的 read＋configure 設定才可人工重開 RX，不因選裝置增加管理權限。明確指定現有 Gateway TOML 的範例：

```powershell
.\.venv\Scripts\uart.exe tui --local --config .\examples\gateway.dual.rx-reopen.toml --record-dir .\recordings
```

沿用本機 Launcher 單身分、一至兩路、115200／8N1／無流控與 DTR／RTS false 的限制。**本機 Q／Ctrl+C 只清理這次建立的 Gateway**。COM 占用、重複啟動或 TCP Port 被使用時會提示，不接管其他程序或自動重試；啟動中退出仍等待清理，清理不明會結束整個工作台。

已有獨立 Gateway（家裡同機或公司 SSH Tunnel）時，改用以下命令：

```powershell
.\.venv\Scripts\uart.exe tui
# 明確使用管理憑證，才提供授權通道的 RX 重開
.\.venv\Scripts\uart.exe --access manage tui
```

先依 [認證初始化](authentication.md) 安裝固定來源，不需每天處理 Token。畫面預先勾選可讀通道，Enter 開始，最多四路；Tab 切換控制項、空白勾選。**遠端 Q／Ctrl+C 只關閉本次 Client，Gateway 與錄製繼續**。不修改 Gateway 錄製設定、不管理 Gateway／Tunnel 程序。Linux 使用 `.venv/bin/python -m pip install -e '.[tui]'` 與 `.venv/bin/uart tui`。

`uart menu` 主畫面新增 **T 多窗格監看**，可選本機／遠端讀取／遠端管理。由同一工作台啟動本機錄製後，返回可用 **L 查看本次錄製**；獨立執行 TUI 結束後，以 **5 瀏覽錄製**選檔。遠端 TUI 不下載 Gateway 錄製。

## 固定操作區

按 **1～4** 或點擊窗格選定 UART，再點按鈕或快捷鍵。所選窗格有外框；沒有權限或狀態不符時，對應按鈕停用。

| 操作 | 行為 |
| --- | --- |
| R 更新 | 查詢狀態／能力，不開 Serial、不重建訂閱 |
| S 監看 | 通道已就緒且沒有活動訂閱時，明確重建即時監看；清空該路畫面，其他窗格繼續 |
| O 重開 | 先查詢核對，點確認或 Y 才人工重開所選遺失／故障／已關閉通道；需 configure 與能力支援 |
| P 暫停 | 只凍結所選畫面，仍接收；再按 P 恢復 |
| PageUp／PageDown、滑鼠滾輪 | 回看所選窗格的有限歷史；回捲後停止追蹤最新 |
| End 最新 | 解除暫停並追蹤最新，不重新連線 |
| C 選通道 | 重新查詢、選擇；套用後重建本次 Client 訂閱，從仍保留的歷史開始；取消保留現有監看 |
| Q／Ctrl+C | 等待本次 SDK／Launcher 清理後離開 |

各路狀態列固定顯示就緒／遺失、訂閱中／重播／即時／停止、觀測 bytes、錄製狀態、修訂與世代前八碼；錯誤優先佔第二行。bytes 不是畫面保留量或前段零遺失證明，錄製完整性仍用 `recording-info` 檢查。

拔除 USB 時該窗格保留舊畫面、標示停止，另一路持續；插回不自動恢復。有管理權限時選該路、O、確認；成功後清空該路舊畫面、解除暫停，自動顯示新世代，不必再按 S。若操作結果不明，先 R 查詢、不直接重送；Gateway Instance 改變則 C 明確重選。任何情境都不執行目標 Confirm、TX Lease 或 UART 指令。

## 顯示與容量界線

每窗格有獨立 SDK Session、增量 UTF-8 解碼與終端模型。快照／訂閱核對由 `client.rx.subscribe_checked` 與 CLI 共用，管理另用短期 SDK 連線。認證及權限不另實作，Server／Protocol／錄製格式不變。

接收直接更新有界模型，每秒最多重畫十次，不為每筆 RX 排入 UI 訊息。每路保留最近 300 行歷史及最多 80×240 的目前畫面；單格最多 16 個 Unicode 碼位，組合符號超限會提示。畫面淘汰不代表 RX Gap；長期保留須先開錄製。原 SDK 256 筆／262144 bytes 事件上限不變，真正溢位／Gap／位移不連續會停止該窗格並告警。

只模擬既有允許的顏色、CR／LF／退格／Tab；裝置 ANSI 不直接輸出主終端。OSC、剪貼簿、任意游標與清畫面控制不執行，不是 IPCAM Shell。縮放改變之後文字折行，既有行不重排，縮窄可能裁切；原始錄製不變。暫停保留有限快照，恢復時中間畫面可能已被淘汰。

本機選裝置產生 `.uart-tui-*.local.toml`，只保存無秘密條件、不覆蓋來源。含序號時以已知序號／VID／PID／位置重新解析；條件變更或模糊時拒絕，需重新選擇。明確開始時保存配置路徑與錄製偏好到 `.uart-workbench.local.json`，不保存 Token、確認資格或 UART 資料。遠端不保存到此偏好。

## 驗證範圍

Linux 自動化使用 Fake Serial、真 TCP／SDK、Textual 無頭 UI 及真 POSIX 終端／pySerial PTY。證據以 [目前狀態](status.md) 與 [開發規範](development.md) 為準。

尚待 Win11／COM3／COM4 與公司 dxan 的實際雙擊、鍵盤、顏色、縮放、拔插／重開及長時間接收；舊版工作台驗收不當作新 TUI 實測。五至六路容量、自動重連、TX、遠端設定編輯及完整終端協定不在本輪。
