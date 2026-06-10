# One-shot setup for the SIVEP-Gripe downloader on Windows (PowerShell).
# Creates venv\, installs deps, downloads Chromium into the project-local
# .playwright-browsers\, and registers the Jupyter kernel. Safe to re-run.
$ErrorActionPreference = "Stop"

# Always operate from the project root (this script's own directory).
Set-Location -Path $PSScriptRoot

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

Write-Host ">> Using interpreter: $(& $python --version) ($python)"

if (-not (Test-Path "venv")) {
    Write-Host ">> Creating virtual environment in .\venv"
    & $python -m venv venv
}

& .\venv\Scripts\Activate.ps1

Write-Host ">> Upgrading pip"
python -m pip install --upgrade pip

Write-Host ">> Installing Python dependencies"
python -m pip install -r requirements.txt

Write-Host ">> Installing Chromium into .\.playwright-browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path $PSScriptRoot ".playwright-browsers")
python -m playwright install chromium

Write-Host ">> Registering Jupyter kernel"
python -m ipykernel install --user `
    --name automation-web-sign-in `
    --display-name "Python (automation-web-sign-in)"

if (-not (Test-Path ".env")) {
    Write-Host ">> NOTE: .env not found - copy .env.example to .env and fill in credentials:"
    Write-Host "       Copy-Item .env.example .env"
}

Write-Host ""
Write-Host ">> Done. Launch with:  .\venv\Scripts\Activate.ps1 ; jupyter lab"
