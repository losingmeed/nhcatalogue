# PyInstaller spec for nhcatalogue (M5).
# Build a single-folder (onedir) app — faster startup and easier debugging than
# onefile; PyInstaller >= 6.10 supports Python 3.13+.
#
#   pip install pyinstaller
#   pyinstaller nhcatalogue.spec --noconfirm
#   → dist/nhcatalogue/nhcatalogue  (run it; needs a graphical session)
#
# Includes: the Python package with SQL migrations + the built UI assets
# (run `cd ui-src && npm run build` first) and a webview-friendly runtime.

import sys
from pathlib import Path

block_cipher = None
UI_DIST = Path("src/nhcatalogue/ui/dist")

datas = [
    ("src/nhcatalogue/db/migrations", "nhcatalogue/db/migrations"),
]
if UI_DIST.exists():
    datas.append((str(UI_DIST), "nhcatalogue/ui/dist"))
else:
    print("WARNING: UI not built — run `cd ui-src && npm install && npm run build` first")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview",
    "webview.platforms.gtk",  # Linux
    "webview.platforms.qt",
    "pystray",
    "PIL",
]

a = Analysis(
    ["src/nhcatalogue/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nhcatalogue",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a console for logs on Linux; set False for clean desktop use
    icon=None,  # add an .ico/.icns/png path here when available
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nhcatalogue",
)
