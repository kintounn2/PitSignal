@echo off
setlocal
cd /d "%~dp0"
title PitSignal - Painel web F1 24
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar.ps1" %*
set "pitsignal_exit=%errorlevel%"
echo.
pause
exit /b %pitsignal_exit%
