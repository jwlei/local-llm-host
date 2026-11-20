@echo off
rem Double-click: stop local-llm-host from starting when you sign in to Windows.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0disable_startup.ps1"
pause
