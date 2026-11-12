@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 找不到專案虛擬環境，請先依 Windows Quickstart 安裝。
    pause >nul
    exit /b 1
)
".venv\Scripts\python.exe" -m uart_gateway.cli remote --pause-on-error
exit /b %errorlevel%
