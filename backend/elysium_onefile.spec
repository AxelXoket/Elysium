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
import sys

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
    # PIL must NOT be excluded (attachments_service imports it at boot).
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

# WHERE A SHIPPED BINARY IS ALLOWED TO COME FROM, and this is not a style rule.
#
# Measured on 22 August 2026 in this build's own Analysis-00.toc: FORTY DLLs
# were being collected from a MiKTeX installation under the building user's
# profile - a LaTeX distribution that happened to put its bin directory on
# PATH. They are `api-ms-win-crt-*` forwarders, the C runtime, so the app was
# shipping its runtime from whatever unrelated program sorted first on the
# machine that built it.
#
# Three things are wrong with that and only the third is theoretical. The
# build is not reproducible: another machine, or the same machine after an
# uninstall, produces a different exe from the same source. The versions are
# unknown: nobody chose them and nothing records what they were. And the
# redistribution terms for the C runtime describe a supported path that does
# not include scraping a third party's install directory.
#
# So the source of every binary is checked rather than trusted. The allowed
# roots are the ones somebody actually chose: this project, the virtualenv it
# builds in, and the Python installation underneath. A binary from anywhere
# else stops the build by name instead of riding into the exe unnoticed.
#
# It is a hard failure, not a filter. Dropping the file silently would leave
# the same question unanswered - what is my runtime - one layer further down.
_ALLOWED_BINARY_ROOTS = tuple(
    os.path.normcase(os.path.abspath(root))
    for root in (BACKEND, os.path.dirname(BACKEND), sys.prefix, sys.base_prefix,
                 os.path.dirname(os.__file__))
)
_WINDOWS_OWN = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))

_strangers = []
for _dest, _src, _kind in a.binaries:
    if not _src:
        continue
    _norm = os.path.normcase(os.path.abspath(_src))
    if _norm.startswith(_ALLOWED_BINARY_ROOTS) or _norm.startswith(_WINDOWS_OWN):
        continue
    _strangers.append((_dest, _src))

if _strangers:
    _lines = "\n".join(f"    {d}  <-  {s}" for d, s in sorted(_strangers))
    raise SystemExit(
        "elysium_onefile.spec refused the build.\n\n"
        f"{len(_strangers)} binaries would be shipped from outside this "
        "project, its virtualenv, its Python installation and Windows "
        "itself:\n\n"
        f"{_lines}\n\n"
        "That is PATH deciding what goes in the exe. Build from a shell whose "
        "PATH does not contain the directory above, or add the root to "
        "_ALLOWED_BINARY_ROOTS in this file with a line saying why it belongs "
        "there."
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
