@echo off
setlocal DisableDelayedExpansion
call "%~dp0run-uart.cmd" remote
exit /b %errorlevel%
