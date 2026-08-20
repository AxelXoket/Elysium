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
    # collect_all sweeps a package whole, which drags webview/__pyinstaller
    # and pythonnet/_pyinstaller in as DATA files - four .py sources that
    # exist only to be read by PyInstaller at build time and that quote the
    # GPL package in their import lines. The excludes list above cannot see
    # datas, so the sources shipped anyway until this filter.
    d = [entry for entry in d if "_pyinstaller" not in entry[0]]
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
    excludes=[
        # Never used by this app, and each drags a large tree behind it.
        "tkinter", "matplotlib", "numpy",
        # The test runner. Note this removes the thin pytest facade only:
        # _pytest (3 modules) and pluggy (8) still ship, because
        # collect_all("keyring") sweeps in keyring/devpi_client.py, which
        # imports pluggy. So this is size, not a closed door.
        "pytest",
        # PyInstaller's Python package is GPL-2.0-or-later. Its bootloader
        # exception names exactly two directories, ./bootloader/ and
        # ./PyInstaller/loader, and the authors relicensed hooks/rthooks to
        # Apache separately, which is direct evidence they did not read the
        # exception as covering everything embedded. 23 modules of
        # PyInstaller.compat, .utils.hooks, .building and .depend were being
        # frozen into the shipped exe, outside that scope, while this project
        # still has no LICENSE of its own. Measured 20 August 2026; the
        # v1.1.5 binary that is already published contains them.
        #
        # They arrived through the two hook packages below, which do
        # module-level `from PyInstaller.utils.hooks import ...` and were
        # added as hiddenimports by the collect_all loop above. Excluding
        # those two is what actually does the work; naming PyInstaller here
        # is belt and braces.
        "PyInstaller",
        "webview.__pyinstaller", "pythonnet._pyinstaller",
        # Both hooks still RUN. PyInstaller discovers hook directories in a
        # separate isolated subprocess through the pyinstaller40 entry point
        # (building/build_main.py, discover_hook_directories), and the
        # excludes list is assigned to the Analysis object afterwards and
        # reaches only the module graph. Verified in the resulting manifest:
        # the WebView2 DLLs, the WinForms interop and Python.Runtime.dll are
        # all still collected, so the CLR window is untouched.
        #
        # 336 modules of syntax highlighting. Reached only from
        # httpx/_main.py, which httpx/__init__.py DOES import at module level
        # - inside a try/except ImportError that falls back to a stub. That
        # module has never once loaded here anyway: it also imports rich,
        # which is not installed. Dead weight the graph walker collected.
        #
        # ONE THING TO KNOW BEFORE ADDING PICTURE CODE. pygments was the only
        # importer of PIL.ImageDraw, ImageDraw2, ImageFont, ImagePath and
        # ImageText, so those five modules and the FreeType binding
        # _imagingft.pyd left with it. attachments_service uses Image,
        # UnidentifiedImageError and ImageOps only, which all stay. Anything
        # that later wants to DRAW on an image or measure text has to name
        # those modules as hiddenimports here; without that it fails at
        # runtime, not at build time.
        "pygments",
        # A PE parser reached only through peutils, which nothing imports,
        # and through PyInstaller's own build-time dependency analysis.
        "pefile",
        # 133 modules of packaging machinery. Nothing here uses setuptools at
        # runtime; keyring finds its backends through importlib.metadata via
        # keyring/compat/py312.py, which is stdlib and stays. pkg_resources
        # is not installed in this environment at all, so it is not listed:
        # a future dependency that pulls it in would need its own line.
        "setuptools",
    ],
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
