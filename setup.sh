#!/usr/bin/env bash
# One-shot setup for the SIVEP-Gripe downloader on Linux / macOS.
# Creates venv/, installs deps, downloads Chromium into the project-local
# .playwright-browsers/, and registers the Jupyter kernel. Safe to re-run.
set -euo pipefail

# Always operate from the project root (this script's own directory).
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo ">> Using interpreter: $("$PYTHON" --version 2>&1) ($PYTHON)"

if [ ! -d venv ]; then
    echo ">> Creating virtual environment in ./venv"
    "$PYTHON" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo ">> Upgrading pip"
python -m pip install --upgrade pip

echo ">> Installing Python dependencies"
python -m pip install -r requirements.txt

echo ">> Installing Chromium into ./.playwright-browsers"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
python -m playwright install chromium

echo ">> Registering Jupyter kernel"
python -m ipykernel install --user \
    --name automation-web-sign-in \
    --display-name "Python (automation-web-sign-in)"

if [ ! -f .env ]; then
    echo ">> NOTE: .env not found — copy .env.example to .env and fill in credentials:"
    echo "       cp .env.example .env"
fi

echo
echo ">> Done. Launch with:  source venv/bin/activate && jupyter lab"
