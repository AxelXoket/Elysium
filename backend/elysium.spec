# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Elysium (one-folder Windows desktop build).

Bundles the FastAPI backend, the built React frontend (served same-origin by
the app itself), the SQLCipher native library, keyring backends, and the
pywebview runtime. Entry point: run_app.py.

Build (from backend/):   pyinstaller elysium.spec
Output:                  dist/Elysium/Elysium.exe

Prereqs: the frontend must be built first (npm run build in ../frontend), and
Windows must have the WebView2 runtime (pre-installed on Win10/11).
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

BACKEND = os.path.abspath(SPECPATH)
FRONTEND_DIST = os.path.abspath(os.path.join(BACKEND, "..", "frontend", "dist"))

# The built SPA (index.html + assets + /elysium-icon.png) served at runtime.
datas = [(FRONTEND_DIST, "frontend_dist")]

# The engine worker halves must exist as REAL FILES on disk: they are run by an
# interpreter that is not ours and cannot see inside the exe. In a onefile
# build the bootloader extracts data files to sys._MEIPASS at launch, which is
# exactly the real path tts/host.py:worker_script() resolves to.
#
# The pinned requirements travel too. They are the measured working
# configuration for each engine - without them "Set up voice" would resolve
# "latest" and quietly install something nobody ever tested.
_WORKER_DIR = os.path.join(BACKEND, "tts", "worker")
datas += [
    (os.path.join(_WORKER_DIR, name), "tts_worker")
    for name in os.listdir(_WORKER_DIR)
    if name.endswith(".py") and name != "__init__.py"
]
datas += [(os.path.join(BACKEND, "tts", "requirements"), "tts/requirements")]
binaries = []

# App modules PyInstaller cannot see through the lazy `from main import app`.
hiddenimports = [
    "main", "config", "database", "crypto", "vault_state",
    "keyring_service", "secrets_service", "legacy_migration",
    "messages_common", "network_client", "openrouter", "proxy_health",
    "attachments_service",
]
hiddenimports += collect_submodules("routers")
hiddenimports += collect_submodules("uvicorn")

# Packages that ship data/native bits or use dynamic imports.
for pkg in (
    "sqlcipher3",     # native SQLCipher library
    "webview",        # pywebview
    "clr_loader",     # pythonnet loader used by pywebview on Windows
    "pythonnet",
    "bottle",         # pywebview's tiny http helper
    "proxy_tools",
    "keyring",        # Windows Credential Locker backend
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_app.py"],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PIL must NOT be excluded: attachments_service.py imports it at boot
    # (image upload validation/resize) - excluding it made the frozen exe die
    # on startup with ModuleNotFoundError. tkinter stays excluded; Pillow
    # works without ImageTk.
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Elysium",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app (no console)
    icon="elysium.ico",
    version="version_info.txt",
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Elysium",
)
