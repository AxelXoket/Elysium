# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller ONE-FILE spec for Elysium.

Produces a single self-contained Elysium.exe (dist/Elysium.exe) that runs
from anywhere - this is the copy committed at the repository ROOT so anyone
downloading the repo can double-click it directly. The one-FOLDER spec
(elysium.spec) remains for local/dev packaging.

Trade-off (accepted): one-file extracts itself to %TEMP% on each launch, so
cold start is a few seconds slower than the folder build.

Build (from backend/):   pyinstaller elysium_onefile.spec
Output:                  dist/Elysium.exe
Prereq: frontend built first (npm run build in ../frontend); WebView2 runtime
on the target machine (checked at startup with a friendly dialog).
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

BACKEND = os.path.abspath(SPECPATH)
FRONTEND_DIST = os.path.abspath(os.path.join(BACKEND, "..", "frontend", "dist"))

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

hiddenimports = [
    "main", "config", "database", "crypto", "vault_state",
    "keyring_service", "secrets_service", "legacy_migration",
    "messages_common", "network_client", "openrouter", "proxy_health",
    "attachments_service",
]
hiddenimports += collect_submodules("routers")
hiddenimports += collect_submodules("uvicorn")

for pkg in (
    "sqlcipher3",
    "webview",
    "clr_loader",
    "pythonnet",
    "bottle",
    "proxy_tools",
    "keyring",
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
    # PIL must NOT be excluded (attachments_service imports it at boot).
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Elysium",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="elysium.ico",
    version="version_info.txt",
    disable_windowed_traceback=False,
)
