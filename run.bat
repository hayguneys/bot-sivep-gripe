@echo off
REM Double-click launcher for the SIVEP-Gripe Qt6 UI on Windows.
cd /d "%~dp0"

if not exist "venv\" (
    echo venv\ not found. Run setup.ps1 first.
    pause
    exit /b 1
)

set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.playwright-browsers"
call "venv\Scripts\python.exe" sivep_ui.py %*
