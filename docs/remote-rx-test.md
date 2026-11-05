# Container 重播與終端顯示合併測試

## 前提與本次範圍

使用者已確認 d3s1 的 dxan 容器可接收 IPCAM UART 資料，dxan 使用 host 網路，直接連到 `127.0.0.1:18081`。本次合併歷史重播修正與可選的終端顯示，讓使用者一次測試；不進行 Tunnel 中斷測試、不新增 UART TX。

保持既有 Windows 只讀 Gateway 與反向 SSH Tunnel 執行。共用 Token 沿用容器的 `$HOME/.config/uart-gateway/com3.token`，不要重新產生或貼出內容。Windows Gateway 不需為這次 Client／CLI 更新而重啟；也不要另開本機一鍵啟動器占用 COM3。

## 從 Git 更新（建議）

在 dxan 容器原本的專案目錄更新 `work` 分支。先檢查本地變更；若曾套用修補檔導致拉取失敗，保留變更及錯誤，不用 reset 或強制覆蓋。

```sh
cd ~/uart-gateway-remote-test
git status --short
git pull --ff-only origin work
```

拉取成功後，可直接使用原本以 editable 模式安裝的 `.venv/bin/uart`，不需重新建立環境或更換 Token。執行下方兩項測試即可；Windows Gateway 與 Tunnel 保持執行。

## 安裝單一測試包（替代方式）

使用 `uart-gateway-rx-terminal-test.zip`，不需套用先前的 v1／v2 修補檔。測試包是本次完整原始碼快照，不含 Git 資料、虛擬環境、Token 或 UART 錄製。

下載到預設 Downloads 後，在 Windows PowerShell 傳到 d3s1：

```powershell
scp "$env:USERPROFILE\Downloads\uart-gateway-rx-terminal-test.zip" victor.hu-vo@d3s1:/tmp/uart-gateway-rx-terminal-test.zip
```

在 d3s1 主機複製到容器，並進入互動式終端：

```sh
podman cp /tmp/uart-gateway-rx-terminal-test.zip dxan:/tmp/uart-gateway-rx-terminal-test.zip
podman exec -it dxan sh
```

在 dxan 容器貼上以下整段。會建立獨立的新目錄與虛擬環境，不覆蓋原本的 `uart-gateway-remote-test`；任何一步失敗都停止後續指令。請保留錯誤，不要繼續下一節。

```sh
uart_test_dir=$(mktemp -d "$HOME/uart-gateway-rx-terminal.XXXXXX") &&
python3 -m zipfile -e /tmp/uart-gateway-rx-terminal-test.zip "$uart_test_dir" &&
cd "$uart_test_dir" &&
python3 -m venv .venv &&
.venv/bin/python -m pip install -e . &&
printf '本次測試目錄：%s\n' "$uart_test_dir"
```

CLI 不需安裝 `[server]` 或 pySerial。以下命令都在剛才的新目錄執行，不需啟用虛擬環境。之後若重新開啟 Shell，先切換到剛才印出的測試目錄。

## 測試一：歷史重播

```sh
.venv/bin/uart --token-file "$HOME/.config/uart-gateway/com3.token" watch ch_uart_1 --duration 60 --replay --text
```

確認歷史資料能持續顯示，不再於相同情境出現 `LocalDataLoss`。這個模式仍會將 CR、退格與 ANSI 跳脫，屬預期行為；下一個測試才處理畫面呈現。

每次 `--replay` 都重新顯示 Gateway 仍保留的歷史，跨次命令看到相同 Log 並非重複輸出的缺陷。Gateway／SDK 佇列仍有上限，輸出持續過慢或歷史超出容量時仍可能明確失敗，不能宣稱任意資料量皆零遺失。若有錯誤，保留訂閱快照與錯誤訊息，不附 Token。

## 測試二：終端顯示

```sh
.venv/bin/uart --token-file "$HOME/.config/uart-gateway/com3.token" watch ch_uart_1 --duration 60 --replay --terminal
```

`--terminal` 取代 `--text`，兩者不能同時指定。請確認：

- CR／LF 正常排版，不再顯示多餘的 `\r`。
- `|`、`/`、`-`、`\` 搭配退格在同一位置更新，不再展開大量 `\x08`；快速重播時可能只看見最後狀態。
- IPCAM 的 ANSI 顏色有呈現，不再顯示 `\x1b[0;32m` 文字。
- 沒有換行的資料也能持續刷新，不會必須等下一筆 UART 資料才顯示。
- 按 Ctrl+C 結束後，Shell 顏色恢復；可重新執行監控。只停止此 CLI，不關閉原 Windows Gateway 或 SSH Tunnel。

需使用支援 ANSI 的互動式終端。若由 d3s1 以 `podman exec` 啟動，請使用 `-it`；輸出到檔案或管線時會拒絕終端模式，改用 `--text` 或預設 JSON／Base64。

終端模式只放行 CR、LF、BS、TAB，以及重設、粗體與前景／背景顏色（標準、256 色與 RGB）。清屏、任意游標定位、裝置查詢、視窗標題、剪貼簿及其他控制字串不轉送。跨 UART 區塊的合法顏色序列會保留狀態；遇缺口、訂閱結束或關閉時丟棄未完成的控制序列，並重設顏色。畫面轉換不改原始 RX bytes，也不傳送 UART 指令。

## 驗證與回報邊界

自動化使用 Linux／Python 3.12、Fake Serial、真 TCP、真 CLI 子程序與 POSIX PTY。PTY 測試檢查輸出的控制位元組，不代表已觀察你的實際終端畫面。dxan 的 Python 3.14、IPCAM 原始歷史、Container 真實顏色／退格效果與 Win11 新模式均待使用者驗證。

請回報兩種模式是否完成、是否仍有 `LocalDataLoss`、顏色與進度符號是否正常，以及 Ctrl+C 後能否再次監控。此輪先完成上述兩項，再安排 Tunnel 中斷與恢復。
