#!/usr/bin/env bash
# Launch the SIVEP-Gripe Qt6 UI on Linux / macOS.
# Activates the project venv and points Playwright at the project-local browsers.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "venv/ not found. Run ./setup.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
exec python sivep_ui.py "$@"
