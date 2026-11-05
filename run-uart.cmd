@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 找不到專案虛擬環境，請先依 Windows Quickstart 安裝 Python 3.12 以上與專案。
    echo 安裝指令：py -3 -m venv .venv
    echo 安裝指令：.venv\Scripts\python.exe -m pip install -e ".[server]"
    echo 按任意鍵關閉此視窗。
    pause >nul
    exit /b 1
)
".venv\Scripts\python.exe" -c "import uart_gateway" >nul 2>&1
if errorlevel 1 (
    echo 找不到 UART Gateway 套件，請先執行：.venv\Scripts\python.exe -m pip install -e ".[server]"
    echo 按任意鍵關閉此視窗。
    pause >nul
    exit /b 1
)
start "" "%~dp0.venv\Scripts\python.exe" -m uart_gateway.cli local --select --read-only --watch --pause-on-error
if errorlevel 1 (
    echo 無法開啟監控視窗，請檢查 Python 安裝與系統權限。
    echo 按任意鍵關閉此視窗。
    pause >nul
    exit /b 1
)
exit /b 0
