# Launch the SIVEP-Gripe Qt6 UI on Windows (PowerShell).
# Activates the project venv and points Playwright at the project-local browsers.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path "venv")) {
    Write-Error "venv\ not found. Run .\setup.ps1 first."
    exit 1
}

& .\venv\Scripts\Activate.ps1
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path $PSScriptRoot ".playwright-browsers")
python sivep_ui.py @args
