# Setup script for local pipeline runner
# Installs required Python packages

Write-Host "Setting up pipeline environment..." -ForegroundColor Cyan

# Activate venv
& .\venv\Scripts\Activate.ps1

# Install required packages for pipeline
Write-Host "Installing Python packages..." -ForegroundColor Yellow
pip install --quiet --upgrade pip

$packages = @(
    "gspread",      # Google Sheets API
    "openpyxl",     # Excel reading
    "pandas",       # Data processing
    "dbfread",      # DBF reading
    "python-dotenv" # .env file support
)

foreach ($pkg in $packages) {
    Write-Host "  Installing $pkg..." -ForegroundColor Gray
    pip install --quiet $pkg
}

Write-Host "✓ Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Set your Google Sheets credentials in .env:"
Write-Host "     - Add GOOGLE_SERVICE_ACCOUNT (full JSON service account)"
Write-Host "     - Add SHEET_ID (your spreadsheet ID)"
Write-Host ""
Write-Host "  2. Run the pipeline:"
Write-Host "     python run_pipeline.py"
Write-Host ""
Write-Host "  Or run parts separately:"
Write-Host "     python run_pipeline.py --sg-only"
Write-Host "     python run_pipeline.py --srag-only"
Write-Host "     python run_pipeline.py --faixa-only"
