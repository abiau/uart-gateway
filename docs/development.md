# 開發規範

## Gateway RX 錄製驗證

`tests/unit/test_recording_text.py` 覆蓋跨分段 UTF-8、控制字元與無效 bytes、缺口／Epoch 解碼切斷、損壞檔警告、原始檔／既有輸出保護、檢查回呼內的輸出失敗，以及無 Token 真 CLI 的 0／2 退出碼。共用掃描器透過同步回呼逐筆輸出，不建立新的無界事件佇列。

`tests/unit/test_recording.py` 注入慢磁碟、佇列滿、容量滿、部分寫入、刷新錯誤及損壞檔案，確認記憶體包含寫入中資料、輪替與總容量有界，未知部分寫入不重送，沒有最終 Footer 不宣稱完整。另測二進位 bytes、Epoch／位移缺口、裝置失去、正常多段雜湊與重複關閉。

`tests/integration/test_recording.py` 使用真 Gateway、SDK、Fake Serial 測無 Client 錄製、兩個 Client 重播不重複寫入、Client 關閉後繼續、錄製失敗的 API 健康事件、兩路 RX 隔離及關閉活躍 Client 後收尾。真 CLI／PTY 測啟動錄製、RX、SIGINT、最終檔案檢查與 Serial 釋放。沒有實體 UART TX，未操作 Win11 硬體。

關閉順序為停止 Listener 接受新連線，取消維護、關閉 Session 並等待 Handler，再等待 Listener 關閉；Python 3.12 的 `wait_closed` 可能等待既有連線，不能排在 Session 清理之前。所有 Channel 停止並送出最後狀態後，才等待 Recorder 排空並收尾。

## 兩條 UART 切片測試紀錄

`tests/unit/test_local_profile.py` 覆蓋配置往返與拒絕未知／認證欄位、檔案及欄位界限、避免覆寫、唯一序號重找 COM、模糊／變更身分、重複端點與名稱、整組取消、CLI 參數及分來源顏色狀態；另驗證單條 SDK 接收失敗不停止另一條，而共用 Writer 失敗會結束全組並關閉連線。持久配置不保存秘密，SDK／Wire Contract 不變。

`tests/integration/test_multi_launcher.py` 以兩個 PTY、真 pySerial、真 Gateway 子程序及 SDK 測試兩路 RX；拔除第一個 PTY 後，第二條仍收到新資料且 Epoch 不變。亦覆蓋第二條被占用時整組撤銷、跨啟動器端點鎖、部分取得鎖失敗後釋放、既有 Listener 保護、取消、真 CLI 載入配置後 SIGINT 清理，以及兩路各 56400 bytes 重播遇到 200 ms 顯示停頓。CLI 程序測試只以 Mock 補上 PTY 枚舉，Serial／Gateway／SDK 路徑保持真實。

上述為 Linux 模擬／虛擬端點證據，不涵蓋 Windows CMD 主控台、兩個 USB Driver 的實體插拔、控制線脈衝或 30 分鐘持續接收。完整執行仍使用 `python -W error::RuntimeWarning -m unittest discover -s tests -v`；本機 Socket／執行緒通知需要執行環境允許。

> 核准更新（2026-11-03）：使用者已在本次對話接受全部建議答案，核准 Phase 0 文件與建議基線。下文「候選／建議／待核准」保留設計來源與理由，現已作為初始採用值；不代表硬體或網路已驗證。Q-11 接線與 UART 參數已由使用者確認；Q-12～Q-14、Q-17 的現場資訊仍待取得。目前實作與驗證進度以專案狀態文件為準。

## 目前狀態與環境規劃

Phase 1 第一批已建立套件、模擬 Gateway、SDK／CLI 與無硬體測試；Serial Backend 已進入第二批並以 Linux PTY 測試；CI（持續整合）、Win11 實機驗證與 Recorder 尚未完成。以下保留完整規劃；實際已建立的項目以專案狀態文件為準。

建議最低 Python 3.12，於 Windows 11 與 Linux Ubuntu 測試；版本政策尚待核准。每個環境使用獨立虛擬環境，依同一 `pyproject.toml` 管理依賴。Windows 執行 Serial Backend（裝置存取實作），Linux 使用 SDK（用戶端開發套件）與 CLI（命令列介面）。不要求 Linux 能操作 Windows COM，也不要求沒有硬體的 CI 裝上實體 Adapter（轉接器）。

Podman Container（容器）的映像、網路與套件管理沿用已存在且經確認的環境；本次不修改 Dockerfile 或 Podman 設定。Network Namespace（網路命名空間）及 SSH 路徑見 [部署文件](deployment.md)。

## 候選 Python Package 結構

以下為完整規劃，只有當批需要的模組才建立；尚未實作的目錄不提前建立。

```text
pyproject.toml                  # 套件、依賴與執行入口
src/uart_gateway/
    __init__.py                # 輕量公開入口，無 I/O 副作用
    serial_backend.py          # pySerial、裝置枚舉與 I/O
    cli.py                     # 參數、輸出與 SDK 操作
    text_renderer.py           # 增量過濾基本排版與有限的 ANSI 顏色
    local_launcher.py          # 本機只讀啟動、就緒檢查與自己的子程序管理
    local_ports.py             # 只讀枚舉顯示、互動選擇及快照重驗
    protocol/
        __init__.py
        messages.py            # 線上資料模型
        codec.py               # JSON Lines、Base64、限制
        errors.py              # 協定錯誤碼
    core/
        __init__.py
        models.py              # 領域模型
        ports.py               # 最小必要的 I/O 與時鐘介面
        channel.py             # 通道狀態、RX 與 TX 協調
        lease.py               # 租約與隔離序號
    server/
        __init__.py
        app.py                 # 組裝與啟停
        config.py              # 設定載入及驗證
        tcp.py                 # TCP、訊框與派送
        auth.py                # 身分與權限
        session.py             # 工作階段與資源清理
        recording.py           # 錄製、輪替與完整性
    client/
        __init__.py
        sdk.py                 # 穩定的公開操作介面
        transport.py           # 連線、請求配對與接收
tests/
    unit/                      # 無網路、無硬體的邏輯測試
    integration/               # 真 TCP 與模擬裝置
    hardware/                  # 明確選用的實體驗證
    fixtures/                  # 固定訊框及二進位測試資料
    fakes.py                   # 可控制的 Serial、Clock 與故障
```

當一個模組仍有清楚責任時，不因程式行數或預想功能拆出更多目錄。TUI（文字介面）、MCP（模型內容協定）及 Firmware Workflow（韌體工作流程）在各自階段才加入，且只能透過 SDK 操作 UART。

## pyproject.toml 規劃

採一個 Python Distribution（發布套件），使用標準專案 Metadata（中繼資料）及 `src` 配置。Build Backend（建置後端）與鎖定工具在實作前選擇，不額外引入多套相同用途工具。

| 項目 | 候選規劃 |
| --- | --- |
| 基礎安裝 | Protocol（通訊協定）、Client SDK、CLI |
| `server` Extra（選用依賴群組） | pySerial 等 Windows Gateway 需要的依賴 |
| `tui` Extra | Phase 2 實際選定 UI 工具後才加入 |
| `dev` Extra | 實際使用的測試、型別及格式檢查工具 |
| Entry Point（執行入口） | `uart-gatewayd` 與 `uart`，尚未存在 |
| 版本 | Distribution Version（套件版本）與 Protocol Version（協定版本）分開 |

Linux 匯入 SDK 不得匯入 Windows 專屬程式、枚舉 COM 或要求 pySerial。Protocol 可以獨立使用，但第一版不需另發一個 Distribution。

## Coding Standards（程式撰寫規範）

- 模組、Class（類別）、Function（函式）與 JSON Key（欄位名稱）使用英文識別名稱；說明、註解及文件字串使用繁體中文。
- 公開介面使用明確型別、穩定錯誤與資源生命週期；避免自由傳遞未驗證的 `dict` 成為所有層的共同介面。
- Core 不依賴 Server、Client、pySerial 或 TUI；線上資料與 Domain Model（領域模型）在 Server 邊界轉換。
- 以必要的 Serial、Clock、Event Sink（事件輸出）介面支援測試，不建立未使用的通用框架。
- asyncio Task（工作）不得直接執行阻塞 Serial／磁碟 I/O；執行緒與 Event Loop（事件迴圈）之間使用有界交接。
- 使用 Monotonic Clock（單調時鐘）判斷期限；UTC Timestamp（時間戳）只作事件對照。
- 不透過全域 Serial Handle（控制代碼）、RX Buffer（緩衝區）或 TX Lock（鎖）耦合多個 Channel。
- 不在 import、UI Callback（回呼）或一般 Retry（重試）中自動送 UART 命令。
- 所有資源清理須可重複呼叫；Client 的慢速輸出、取消或關閉不得留下 Lease（租約）。

## Logging 與 Exception Handling

Logging（紀錄）分成一般診斷、UART Recording（錄製）及 Audit（稽核）。一般 Log 使用固定欄位連結 Instance（執行個體）、Channel、Session（工作階段）、Request ID（請求識別）及 TX ID；不保存 Token（憑證）、秘密檔案內容或完整環境變數。

原始 UART bytes 保留在錄製／擷取資料中；人類畫面用增量 Decoder（解碼器）處理文字，無效字元不得破壞原始資料。控制字元預設安全顯示，真正原始終端模式需明確選擇。UART 內容對 Agent 是不可信資料，不能成為自行授權指令的來源。

Exception Handling（例外處理）需區分：設定錯誤、認證失敗、Port 占用、裝置遺失、正常 Read Timeout（讀取逾時）、Partial Write（部分寫入）、TX Unknown（結果不明）、慢速 Client 與磁碟故障。不能只回覆「連線失敗」，也不能廣泛捕捉後繼續宣稱正常。

每個失敗應更新正確的 Channel／Session／Recorder 狀態，傳回穩定錯誤碼，保留不含秘密的診斷。Stack Trace（堆疊追蹤）留在受控診斷紀錄，不直接送給一般 Client。Client 本地請求逾時不等於取消已接受的 TX。

## 無實體 UART 的測試

Fake Serial（模擬序列裝置）必須能控制輸入 bytes、空讀取、延遲、裝置遺失、短寫入、逾時及關閉競爭；不能只是所有方法回傳成功。Fake Clock（模擬時鐘）可前進時間以測試 Lease 到期，不用長時間 sleep。

Unit Testing（單元測試）涵蓋：

- JSON Lines 分段、黏包、超限、重複 Key、無效 UTF-8、非法 Base64 與任意二進位往返。
- 版本協商、必要欄位、未知可選欄位與穩定 Error Code（錯誤碼）。
- Binding Revision（綁定修訂）、Stream Epoch（資料流世代）、Fencing Token（隔離序號）與 Dispatch（真正派送）前再驗證。
- Ring（環狀緩衝）淘汰、Replay（重播）邊界、跨 Chunk（區塊）Offset（位移）與 Gap（缺口）。
- TX 狀態、去重快取、ID 衝突、快取容量及不自動重送。

Integration Testing（整合測試）使用真實 TCP Server + Fake Serial + 真實 SDK，驗證完整路徑與清理：多 Reader（讀取者）、一個 Writer（寫入者）、慢 Client、網路中斷、重連、新 Epoch、Recorder 失敗及兩個 Fake Channel 的隔離。

Fake Gateway（模擬伺服器）只用於 SDK 接收異常、相容性與錯誤注入，不能代替真 Server 整合測試。Golden Frames（固定標準訊框）來自 Protocol 規格，避免兩端共同寫錯仍互相通過。

Virtual UART（虛擬序列端點）、Linux PTY（偽終端機）或 pySerial `loop://` 可補充傳輸測試，但不能證明 Windows USB Driver、實際插拔或控制線行為正確。

## Hardware Testing（硬體測試）

Phase 1 第一個目標是 COM3、115200、8N1。執行前確認 Adapter、接線電壓、共地、DTR／RTS 與目標板狀態。硬體測試明確標記並由有硬體的環境選用，不讓一般 CI 失敗於缺少 COM。

至少驗證占用、拔除、重新枚舉、長時間已知序號資料流、連續 Log、Write Timeout、關閉與寫入競爭、錄製失敗、Tunnel 中斷及重新 Confirm。使用可計數來源才能量測傳輸缺口；看到文字正常不代表零遺失。

每次保存 OS、Python、Driver、Adapter、Baud Rate、測試時間、資料量與結果。未測試的硬體及速率不標成支援保證。

## CI/CD 與變更流程

未來 CI 首先跑 Windows／Linux 的無硬體 Unit 與 Integration、文件連結、JSON 範例及相容性測試。候選 Python 矩陣為 3.12、3.13；實際支援範圍待核准。Performance Testing（效能測試）依核准的 Channel／Client／Baud Rate 上限建立，不憑空指定 SLA（服務水準）。

CD（持續交付）與發布流程留待可用版本形成後設計；目前不自動發布或部署；提交與推送依當次使用者授權範圍進行。變更順序為讀規格 → 分析影響 → 最小實作 → 對應驗證 → 更新單一權威文件與 [狀態](status.md)。Protocol 變更需同步維護範例及相容性證據。

## 第二批的實體 Serial 驗證

實體模式安裝 `python -m pip install -e '.[server]'`，再以 `uart-gatewayd --serial --config examples/gateway.com3.toml` 啟動。Gateway 的 `--list-ports` 只枚舉，不開啟裝置；CLI／SDK 仍不直接呼叫 Serial。Win11 只讀驗證見 [接收步驟](windows-quickstart.md)。

新增測試分為 Driver 替身（開啟順序、控制線、匹配、錯誤、端點預留）、生命週期晚到結果，以及 POSIX PTY 的真 pySerial 整合。PTY 測試需要 `[server]` 與 POSIX，不能代替 Windows Driver 或 IPCAM 硬體證據。只有 Linux SDK 的環境不需要 pySerial，模組匯入也不開啟裝置。

## 本機啟動器驗證

`tests/unit/test_local_launcher.py` 驗證跨程序端點鎖、Token 管理、就緒重試與失敗、權限與 UART 設定核對，以及只停止自己的子程序。`tests/integration/test_local_launcher.py` 在 POSIX 搭配 pySerial，以真子程序及 PTY 驗證 RX、正常／取消清理、缺少端點、重複啟動、既有 Gateway 保護、CLI 的 SIGINT 與立即重啟。

完整測試沿用 `python -m unittest discover -s tests -v`。Windows 或未安裝 `[server]` 時，PTY 整合會明確略過，不能宣稱完整測試通過。Windows 的雙擊主控台、檔案鎖與實體 COM3 另按 [Quickstart](windows-quickstart.md) 驗收；自動化結果及尚未驗證範圍記於 [狀態](status.md)。

## CLI 文字重播與取消

SDK 每次排入事件後讓出執行機會，避免已緩衝的歷史重播長時間占住 Reader。事件數量與位元組上限不變；完全不消費或輸出持續過慢時，仍回報 `LocalDataLoss`，不默默刪除資料或擴大容量。

`watch --text` 以 20 ms 為刷新目標，或累積達 16 Ki 字元時排入顯示佇列。組合批次的暫存上限為門檻加一個合法 RX 區塊；控制字元跳脫後仍有界。20 ms 不是系統即時性保證，會受排程與終端速度影響。跨區塊 UTF-8、沒有換行的文字、Replay／Live 標示與訂閱終止均需測試。

事件消費與畫面 Writer 是兩個工作，前者不等待每次 stdout／stderr 寫入。監控輸出共用有序佇列，最多 256 項、262144 個 UTF-8 bytes，用量包含正在寫入的批次。SDK 的事件數量／位元組上限維持不變；短暫畫面停頓可由緊湊文字緩衝承接，長期輸出跟不上則回報明確的「CLI 顯示佇列已滿」，不丟棄資料或無限擴張。Writer 失敗會喚醒等待 UART 的監控工作。

批次在 await 前移出暫存，不因取消或部分輸出失敗而重試。一次執行緒工作完成 write 與 flush；Ctrl+C 或觀測期限取消時，等待已開始的輸出工作收斂，再傳遞取消。因此 OS 若阻塞 stdout，結束也會等待，不能宣稱 Python 能安全強殺該 I/O。原始錯誤必須保留，清理失敗附加於例外，不取代資料遺失／斷線原因；SDK 的 TimeoutError 也不能當作正常觀測結束。

關閉時先停止事件消費並取消訂閱，再等已排入的畫面輸出完成。Writer 在正常收尾恢復顏色，即使 Producer 因佇列滿載失敗也會執行；輸出裝置本身已失效時不重試不明的部分寫入。

`watch --terminal` 與 `local --terminal` 共用上述批次輸出及只讀接收路徑；前者與 `--text` 互斥。`TerminalRenderer` 逐段處理文字，只轉送基本排版與驗證過的 SGR；CSI 參數最多保留 128 字元，OSC／DCS 等控制字串只保留解析狀態、不累積內容。缺口與訂閱終止會切斷解碼／控制序列狀態，結束時恢復顏色。TTY 檢查在連線之前，避免重導向輸出包含作用中的終端控制碼。

測試涵蓋控制序列每個切割點、UTF-8 跨區塊、顏色與進度符號、未知控制碼、超長與未完成序列、原樣保留字面上的跳脫文字、取消／顏色重設，以及兩種模式的大量小區塊重播。真 CLI／PTY 另驗證互動輸出與管線拒絕；不能用位元組測試宣稱真實終端畫面已由 Agent 驗證。現場操作見 [Container 合併測試](remote-rx-test.md)。

## 本機端點選擇驗證

`tests/unit/test_local_ports.py` 用 Mock 清單驗證描述與自然排序、未提供資訊、控制字元跳脫、無裝置、無效輸入、重新整理、取消／EOF／Ctrl+C、同名端點變更及重複端點。選單在事件迴圈建立前輸入；測試確認取消不建立迴圈或 Gateway。`uart ports` 不讀取認證、不建立 SDK 連線或 Serial Handle。

`test_local_launcher.py` 的單元測試驗證所選身分完整進入既有 TOML 載入器與 SDK Ready 檢查；整合測試用 POSIX PTY 驗證選擇後真子程序 RX、端點消失清理、占用保護與選單輸入期間 SIGINT。描述只放在 Backend 的本機顯示資料，Core／Wire Model 不增加欄位。這些測試不代替 Windows 枚舉、CMD 選單或 USB 插拔實測。

## 互動模式手動恢復

`_run_selected` 在同步選單與每次 `asyncio.run` 之間協調，不能在事件迴圈裡呼叫 input 或留下等待 input 的執行緒。監控結束碼 2 或已知啟動／連線錯誤，必須等 `launch_local` 清理完成後才重新選擇；取消、正常期限、參數錯誤或 `LauncherCleanupError` 均離開流程。未知程式錯誤不當作可重試的裝置故障。固定端點與遠端 watch 的既有退出碼保留。

單元測試覆蓋新的明確選擇、改選端點、開啟／連線失敗、取消、參數預檢與清理失敗防護。`InteractiveRecoveryTests` 使用真 Gateway 子程序、pySerial／PTY 與 SDK，在收到第一筆資料後關閉 PTY master 模擬拔除；第二次選擇前檢查舊程序已結束、臨時設定已移除、鎖可取得，再選另一個端點驗證 RX 與新的 Token／Instance／Epoch。這不代表 Win11 插拔恢復或 30 分鐘接收已驗證。


恢復選單以 `recovering=True` 區分輸入語意：初次 Enter 取消，恢復期間 Enter 只刷新；Q／Ctrl+C 離開，EOF 明確提示後結束，不重讀 EOF。回歸測試覆蓋恢復選單的空白 Enter、新清單明確選擇與 EOF 訊息；真程序／PTY 恢復測試在舊 Gateway 結束後先輸入空白，確認沒有退出或建立第二個 Gateway，再以編號選新端點並接收。
