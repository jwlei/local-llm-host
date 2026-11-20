@echo off
rem Double-click: start local-llm-host by itself, headless, when you sign in to Windows.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_startup.ps1" %*
pause
