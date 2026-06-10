# PyInstaller spec for bot-sivep-gripe (Windows .exe).
# Builds a small GUI exe; Chromium is downloaded on first run (not bundled).
#
#   pyinstaller bot-sivep-gripe.spec
#
# Notes:
#  - Playwright ships a Node-based driver that MUST be collected as data.
#  - PySide6 is collected via collect hooks.
#  - .playwright-browsers/ is intentionally NOT bundled (huge; fetched at runtime).

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = []

# Playwright: bundle the driver + package data so 'playwright install' works at runtime.
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hidden

# dbfread / dotenv have no native deps but ensure they are importable.
hiddenimports += ["dbfread", "dotenv"]

# PySide6 essentials (QtWidgets/QtCore/QtGui). collect_all pulls the needed Qt plugins.
for mod in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
    hiddenimports.append(mod)

block_cipher = None

a = Analysis(
    ["sivep_ui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "jupyterlab", "notebook", "nbconvert", "nbclient", "jupyter_server",
        "IPython", "ipykernel", "tornado", "matplotlib",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bot-sivep-gripe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app, no console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="bot-sivep-gripe",
)
