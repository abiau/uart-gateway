@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
set PYTHONUTF8=1
cd /d "%~dp0"
set "UART_ENTRY=menu"
if "%~1"=="remote" set "UART_ENTRY=remote"
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
".venv\Scripts\python.exe" -m uart_gateway.cli %UART_ENTRY% --pause-on-error
exit /b %errorlevel%
